# French archival transcription

Transcribe only text visibly present in the supplied scans. Preserve French spelling, accents, names, dates, letterhead, marginalia, signatures and paragraph order. Do not translate, summarize, modernize or complete text from historical knowledge.

Return each scan separately and in the supplied order. Use explicit uncertainty markers:

- `[illisible]`
- `[manuscrit illisible]`
- `[lacune]`
- `[masqué]`
- `[rogné]`
- `[signature illisible]`
- `[mot?]` for a doubtful reading.

Return JSON only:

```json
{
  "document_id": "document-0001",
  "pages": [
    {"scan_id": "scan.jpg", "french_transcription": "...", "notes": [], "needs_review": false}
  ],
  "document_notes": [],
  "needs_review": false
}
```
