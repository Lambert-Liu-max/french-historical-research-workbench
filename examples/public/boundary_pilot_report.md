> **公开Pilot说明**：以下10项均来自公开的Gallica议会记录，用于校准“命中页不等于完整事件”的边界规则。它是方法形成阶段的人工审查记录，不是正式模型评测结果。

# Landry boundary-calibration sample: first-pass report

This report records boundary decisions after downloading all 34 hit pages and 28 targeted context pages. It is a calibration artifact, not yet the final French corpus.

- Sample events: 10
- Cached sample-related scans: 75
- API failures in these two batches: 0
- LLM use: none
- Boundary status counts: negative_case_complete=3, provisional_complete=6, requires_internal_split=1

| Event | Date | Chamber | Decision | Status | Scan / printed pages |
|---|---|---|---|---|---|
| bpt6k6462381x-e001 | 1929-03-12 | Chambre des députés | include | provisional_complete | f10–f13 / p. 940–943 |
| bpt6k63276081-e001 | 1929-11-28 | Chambre des députés | include_contextual_discussion | provisional_complete | f39–f45 / p. 3645–3651 |
| bpt6k6455054n-e001 | 1930-03-11 | Chambre des députés | split | requires_internal_split | f2–f3 / p. 988–989; f4–f7 / p. 990–993; f8–f8 / p. 994–994 |
| bpt6k64550537-e002 | 1930-03-08 | Chambre des députés | include | provisional_complete | f17–f20 / p. 973–976 |
| bpt6k64279712-e002 | 1931-02-24 | Chambre des députés | split_and_expand | provisional_complete | f25–f25 / p. 1129–1129; f27–f41 / p. 1131–1145 |
| bpt6k6220637b-e001 | 1931-03-26 | Sénat | exclude_from_speech_corpus_keep_metadata | negative_case_complete | f1–f1 / p. 557–557 |
| bpt6k6434719r-e002 | 1931-06-25 | Sénat | include_recovered_from_summary | provisional_complete | f3–f5 / p. 1113–1115 |
| bpt6k62368961-e003 | 1932-03-10 | Chambre des députés | exclude_from_contemporaneous_landry_speech_keep_context | negative_case_complete | f51–f55 / p. 1325–1329 |
| bpt6k6434748g-e001 | 1932-01-21 | Sénat | include | provisional_complete | f12–f16 / p. 36–40 |
| bpt6k6467307x-e001 | 1933-04-12 | Chambre des députés | exclude_from_contemporaneous_landry_speech_keep_context | negative_case_complete | f9–f9 / p. 2221–2221 |

## Event assessments

### bpt6k6462381x-e001

- Type: contemporaneous_speech_with_interjections
- Identity: confirmed
- Evidence: Scan 10 explicitly assigns the floor to M. Landry and begins 'M. Adolphe Landry'. Scan 9 is a vote/list context; scan 14 has already moved to another speech.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6462381x

### bpt6k63276081-e001

- Type: budget_debate_using_landry_report_with_brief_reply
- Identity: confirmed
- Evidence: Scans 37-38 are vote lists. The substantive labour, health, mortality and population discussion starts on scan 39; speakers repeatedly engage Landry's report and Landry replies on scan 45. Scan 46 continues into a different labour-policy development.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k63276081

### bpt6k6455054n-e001

- Type: multiple_article_level_interventions
- Identity: confirmed
- Evidence: The original gap-based cluster crosses separate articles 73, 74 and 74 F/76. Each has a new formal floor assignment or article heading and must become its own corpus event.
- Next action: detect_article_line_boundaries
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6455054n

### bpt6k64550537-e002

- Type: article_debate_speech_and_replies
- Identity: confirmed
- Evidence: Scan 17 opens article 71 and its maternity-allocation question; Landry speaks on scan 18, direct replies continue on scans 19-20, after which the sitting advances.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k64550537

### bpt6k64279712-e002

- Type: procedural_request_plus_long_unemployment_debate
- Identity: confirmed
- Evidence: Scan 25 is a distinct request for immediate discussion. Scans 27 onward form the unemployment debate in which Landry speaks as minister; general discussion closes on scan 38, followed by amendments and adoption through scan 41 before the agenda changes.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k64279712

### bpt6k6220637b-e001

- Type: ministerial_deposit
- Identity: confirmed
- Evidence: Landry deposits a text on behalf of ministers; this is parliamentary activity but not a debate intervention with a response chain.
- Next action: route_to_parliamentary_acts_table
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6220637b

### bpt6k6434719r-e002

- Type: general_discussion_recovered_from_agenda_summary
- Identity: confirmed
- Evidence: The end-of-issue summary on scan 24 points to Chauveau and Landry. The body starts under the social-insurance bill heading on scan 3, continues with Chauveau through scan 5, where Landry replies and the president closes general discussion.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6434719r

### bpt6k62368961-e003

- Type: backward_reference_by_other_speakers
- Identity: probable
- Evidence: The current speakers refer retrospectively to replies or decisions by Landry; the cached surrounding budget chapters do not show a current Landry intervention.
- Next action: route_to_reception_and_reference_table
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k62368961

### bpt6k6434748g-e001

- Type: bill_text_general_discussion_and_ministerial_reply
- Identity: confirmed
- Evidence: The bill and ministerial signature begin on scan 12, the general discussion develops through scans 13-15, Landry asks for the floor on scan 15, and scan 16 records closure of general discussion and passage to articles.
- Next action: manual_line_boundary_review
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6434748g

### bpt6k6467307x-e001

- Type: historical_quotation
- Identity: confirmed
- Evidence: A 1933 speaker quotes what Landry said when he was formerly a deputy; Landry is not the current speaker.
- Next action: route_to_reception_and_reference_table
- Document ARK: https://gallica.bnf.fr/ark:/12148/bpt6k6467307x

## Method correction established by the pilot

A ContentSearch hit cluster is not equivalent to one debate event. The production workflow must first classify hits, split at article or agenda transitions, recover bodies referenced only by summaries, and retain deposits, historical quotations, votes, and retrospective mentions in separate tables. Only then should line-level beginnings and endings be fixed for the speech corpus.
