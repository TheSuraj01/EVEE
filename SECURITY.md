# Security policy

## Reporting

Please **don't open a public issue** for a vulnerability. Use GitHub's private security
advisory ("Report a vulnerability") on this repository. Expect an acknowledgement within
72 hours.

Useful report: the spoken phrase or payload, the path you believe it takes through the
code, and what it achieved.

## In scope

- Anything that executes an action without a policy `ALLOW` verdict
- Escaping the intent allowlist (reaching an unregistered action)
- A plugin exceeding its declared permissions
- A transcript, audio, or the API key leaving the machine other than as documented in
  `docs/privacy.md`
- Secrets reaching logs, diagnostics, crash output or a release artifact
- Bypassing the confirmation prompt for a high-risk command

## Out of scope

- Someone physically present speaking commands at your unlocked machine — that is the
  product working as designed. Use the mute control.
- Whisper mishearing a word (accuracy, not security), unless it reliably steers to a
  high-risk command without confirmation.
- Vulnerabilities in Groq's service — report those to Groq.

## Design commitments

- The cloud model cannot return executable content: its output space is an enum generated
  from the local command registry, and membership is re-checked locally.
- `safety.allow_arbitrary_shell` and `allow_arbitrary_python` are permanently false. A PR
  that enables either will be rejected.
- `tests/integration/test_contracts.py` fails the build on any dynamic execution in `src/`.

See `docs/security-model.md` for the full threat model.
