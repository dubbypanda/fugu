# Command reference

Additional flags and options for the Fugu installer and launcher.

## Installer flags

`bash ~/.fugu/scripts/install.sh [flag]`. Run with no flag to install and deploy.

| Flag | What it does |
| --- | --- |
| (none) | Install and pin the Codex CLI, then deploy the Fugu config |
| `--set-key` | Re-prompt for and store the Sakana API key, no redeploy |
| `--remove-config` | Cleanly undo the deployed config |
| `--pinned-version X.Y.Z` | Pin a specific Codex version instead of the default |
| `--force` | Deploy even if the installed Codex version does not match the target |
| `--dry-run` | Show what would happen and change nothing |
| `-y`, `--yes` | Assume yes, for non-interactive use |
| `-h`, `--help` | Full list of flags and environment variables |

Non-interactive install (for CI or provisioning):

```bash
SAKANA_API_KEY=your_key bash ~/.fugu/scripts/install.sh --yes
```

## Launcher flags

`codex-fugu` runs `codex -p fugu` and, at most once a day, checks this repo for config updates and offers to apply them. It never blocks launch, and any arguments you pass go straight to Codex.

| Flag | What it does |
| --- | --- |
| `--status` | Show the installed version, the pinned target, and update state |
| `--set-key` | Rotate the stored Sakana API key |
| `--check` | Check for a config update now instead of waiting for the daily check |
| `--recheck` | Re-enable update prompts you previously dismissed, then check |
| `--no-update` | Skip the update check for this launch |

Set `CODEX_FUGU_NO_UPDATE=1` to turn update checks off for good.
