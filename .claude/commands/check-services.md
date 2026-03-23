Check that all external services the newsbot depends on are running and accessible. Verify each of the following:

1. **Ollama**: Hit `http://localhost:11434/api/tags` to confirm Ollama is running and list available models. Specifically check that `gpt-oss:20b` (categorization) and `nomic-embed-text` (embeddings) are loaded.

2. **Bot process**: Check if `data/bot.pid` exists and whether that PID is actually running.

3. **gallery-dl**: Run `gallery-dl --version` to confirm it's installed and accessible.

4. **Tesseract OCR**: Run `tesseract --version` to confirm OCR is available (since `OCR_ENABLED = True` in config).

5. **Dashboard**: Try to reach `http://localhost:8000` to see if the dashboard is running.

Report a clear pass/fail for each service with any relevant version info or error details.
