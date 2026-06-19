API key now lives in ~/.codex/.env

Your Sakana API key is now stored in ~/.codex/.env (file mode 0600), which Codex loads automatically on startup. The installer no longer adds a line to your shell startup file (~/.bashrc or ~/.zshrc), so the key works the same in every shell, IDE, and cron job without opening a new terminal.

Nothing is required on your part. codex-fugu keeps working as before.

If you set Fugu up with an earlier build, you may have leftover files from the old method. They are harmless, but you can remove them if you want a clean setup:

  rm -f ~/.config/fugu/env

and delete the block between these two markers in your ~/.bashrc (or ~/.zshrc):

  # >>> fugu:env >>>
  ... (one source line) ...
  # <<< fugu:env <<<
