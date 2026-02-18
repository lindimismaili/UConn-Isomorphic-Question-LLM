# Shared Contracts

## extracted_document.json
```json
{
  "source": {
    "path": "uploads/file.pdf",
    "type": "pdf|docx|image",
    "filename": "file.pdf",
    "sha256": "..."
  },
  "metadata": {
    "title": null,
    "author": null,
    "created_at": null,
    "modified_at": null,
    "page_count": null,
    "image": null,
    "extra": {}
  },
  "text": "full flattened text",
  "blocks": [
    {
      "block_id": "p1b1",
      "page": 1,
      "kind": "paragraph|table|ocr",
      "text": "...",
      "confidence": null
    }
  ]
}
```

## identified_questions.json
```json
{
  "title": null,
  "author": null,
  "date": null,
  "other": {},
  "questions": [
    {
      "question_id": "q1",
      "stem": "...",
      "choices": [
        {"label": "A", "text": "..."},
        {"label": "B", "text": "..."}
      ],
      "answer_key": null,
      "source_block_ids": ["p1b1"]
    }
  ]
}
```

## answer_analysis.json
```json
{
  "question_id": "q1",
  "explicit_answer_found": false,
  "explicit_evidence": null,
  "reasoned_answer": "B",
  "confidence": 0.0,
  "checks": ["algebra", "units", "edge-case"]
}
```

## generation_output.json
```json
{
  "question_id": "new_q1",
  "stem": "...",
  "choices": [],
  "correct_answer": "...",
  "distractors": [],
  "solution_process": [],
  "quick_reason": "...",
  "difficulty": {"label": "medium", "score": 5},
  "theme": [],
  "concepts": []
}
```

## latex_payload.json
```json
{
  "template_path": "templates/question.tex",
  "output_tex_path": "outputs/questions/new_q1.tex",
  "data": {}
}
```
