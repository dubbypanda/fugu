Fixed: installing the pinned Codex could fail with a "codex-code-mode-host" error

Fugu now installs Codex using the installer that OpenAI publishes with the pinned Codex release, instead of the always-latest installer. OpenAI recently changed the always-latest installer so that it expects a file that Codex releases older than 0.143.0 do not contain, which made a fresh Fugu install fail on every platform. This update also makes the installer report what actually went wrong instead of guessing.

Nothing is required on your part. Your existing Codex install was never affected, and installs, repairs and version rollbacks work again.

If a fresh install of Fugu on some machine failed for you or a teammate with an error like "chmod: .../bin/codex-code-mode-host: No such file or directory", or with a warning blaming a GitHub rate limit, that was this bug — nothing was rate limited, and it was not a permissions problem. That machine still holds the copy of Fugu it cloned during the failed attempt, and simply re-running the one-line install reuses it, so remove it first and then re-run:

  rm -rf ~/.fugu && curl -fsSL https://sakana.ai/fugu/install | bash

If you worked around the failure by installing Codex with "sudo npm install -g @openai/codex", that leaves a root-owned copy earlier on your PATH than the pinned one, and Fugu will warn you that it shadows the pinned version. Remove it with "sudo npm uninstall -g @openai/codex" and re-run the one-line install.
