@echo off
REM Launch Claude Code using local Ollama instead of Anthropic API
set ANTHROPIC_BASE_URL=http://localhost:11434
set ANTHROPIC_API_KEY=ollama

echo [Ollama] Starting Claude Code with llama3.2 (local)...
claude --model llama3.2:latest
