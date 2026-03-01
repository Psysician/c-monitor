# Provider Compatibility Matrix

| Capability | Claude (`--provider claude`) | Codex (`--provider codex`) | Dual (`--provider both`) |
|---|---:|---:|---:|
| Realtime monitoring | PASS | PASS | PASS |
| Daily table view | PASS | PASS | PASS |
| Monthly table view | PASS | PASS | PASS |
| Startup token limit (`custom`) | PASS | PASS | PASS (merged blocks) |
| Limit-message detection | PASS | N/A | PASS (Claude-only detection) |
| Active-session UI | PASS | PASS | PASS (combined active sessions) |
| Memory metrics (`rss_current/peak/p95`) | PASS | PASS | PASS |

## Notes

- Limit-message parsing is currently Claude-specific by design.
- Dual-provider table mode merges period rows by date/month and sums tokens/cost.
- Dual-provider realtime mode merges active sessions into one combined display payload.
