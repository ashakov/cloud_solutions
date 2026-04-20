"""
LLM Reasoning Engine — Phuket Invest-Auditor
Uses google-generativeai SDK with compatibility shim for system_instruction.

The system_instruction constructor param was added in google-generativeai 0.5.
This module works with both old (< 0.5) and new (>= 0.5) SDK versions.
"""
import json
import logging
import re
from dataclasses import dataclass

import google.generativeai as genai

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a Senior Real Estate Investment Analyst specializing in the Phuket \
property market (2026). You produce strict, mathematically justified financial audit reports \
in Russian.

Rules:
1. NEVER trust developer-claimed yields. Always stress-test with real occupancy data.
2. Always deduct: transfer tax (1.1% leasehold / 3.3% freehold), sinking fund, CAM fees, \
furniture package, agency fee (12%), property tax (~0.3% of price/year), maintenance reserve.
3. Flag RED FLAGS: missing hotel licence for short-term rental, leasehold at freehold prices, \
off-plan risk, low liquidity (residential zone).
4. Use the provided market benchmark data (p25/median/p75 per m²) as ground truth.
5. Format output as structured Markdown with tables. No fluff.
"""

# ── Compatibility shim ────────────────────────────────────────────────────────

def _build_model(model_name: str) -> genai.GenerativeModel:
    """
    Creates GenerativeModel compatible with both old and new SDK versions.

    google-generativeai < 0.5: system_instruction not supported in constructor.
    google-generativeai >= 0.5: system_instruction works directly.
    """
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        # New API (>= 0.5.0)
        return genai.GenerativeModel(
            model_name=model_name,
            system_instruction=SYSTEM_PROMPT,
        )
    except TypeError:
        # Old API (< 0.5.0) — system_instruction goes into the first user turn
        logger.warning(
            "google-generativeai < 0.5 detected: system_instruction not supported "
            "in constructor. Falling back to prompt injection. "
            "Run: pip install --upgrade google-generativeai"
        )
        return genai.GenerativeModel(model_name=model_name)


@dataclass
class AuditResult:
    report_md: str
    verdict: str          # BUY | HOLD | AVOID
    net_roi_pct: float | None
    red_flags: list[str]
    warnings: list[str]


class LLMEngine:
    """Wraps Gemini for property audit generation."""

    MODEL_NAME = "gemini-1.5-pro-latest"

    def __init__(self) -> None:
        self.model = _build_model(self.MODEL_NAME)
        self._uses_legacy_api = "system_instruction" not in str(
            genai.GenerativeModel.__init__.__doc__ or ""
        )

    def _build_prompt(self, property_data: dict, benchmarks: dict | None) -> str:
        """Assembles the full prompt for the LLM."""
        bench_block = ""
        if benchmarks:
            bench_block = (
                "\n## Market Benchmarks (district data from our DB)\n"
                f"```json\n{json.dumps(benchmarks, ensure_ascii=False, indent=2)}\n```\n"
            )

        prompt = (
            f"## Property to Audit\n"
            f"```json\n{json.dumps(property_data, ensure_ascii=False, indent=2)}\n```\n"
            f"{bench_block}\n"
            "Produce a full investment audit report in Russian. "
            "Include: Executive Summary, Financial Scorecard (True Entry Cost, Gross Yield, "
            "OPEX breakdown, Net ROI, 5-year Capital Growth, CAGR), Pros & Cons, "
            "Red Flags, AI Market Insight, and recommended strategy."
        )

        # For legacy SDK: prepend system instruction into the user message
        if self._uses_legacy_api:
            prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"

        return prompt

    async def audit(
        self,
        property_data: dict,
        benchmarks: dict | None = None,
    ) -> AuditResult:
        """Generate a full investment audit report."""
        prompt = self._build_prompt(property_data, benchmarks)

        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.2,   # low temp = consistent financial analysis
                    max_output_tokens=4096,
                ),
            )
            report_md = response.text
        except Exception as exc:
            logger.error("LLM generation error: %s", exc)
            raise

        return AuditResult(
            report_md=report_md,
            verdict=self._extract_verdict(report_md),
            net_roi_pct=self._extract_roi(report_md),
            red_flags=self._extract_red_flags(report_md),
            warnings=[],
        )

    # ── Parsers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_verdict(md: str) -> str:
        m = re.search(r"\*{0,2}Вердикт[:\*\s]+([A-Z]+)", md, re.IGNORECASE)
        if m:
            word = m.group(1).upper()
            if "BUY" in word or "КУПИТЬ" in word:
                return "BUY"
            if "AVOID" in word or "ОТКАЗ" in word or "НЕТ" in word:
                return "AVOID"
        return "HOLD"

    @staticmethod
    def _extract_roi(md: str) -> float | None:
        m = re.search(r"Net ROI[^\d]*?([\d]+\.?[\d]*)\s*%", md, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_red_flags(md: str) -> list[str]:
        flags = []
        # Find lines after "Красные флаги" / "Red Flags"
        in_flags = False
        for line in md.splitlines():
            lower = line.lower()
            if "красн" in lower or "red flag" in lower:
                in_flags = True
                continue
            if in_flags:
                stripped = line.strip("-•* ")
                if stripped and len(stripped) > 5:
                    flags.append(stripped)
                if line.startswith("#") and flags:
                    break
        return flags[:5]
