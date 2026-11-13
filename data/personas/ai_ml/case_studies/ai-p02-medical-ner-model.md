---
id: cs-ai-p02
project_id: ai-p02
type: case_study
persona: ai_ml
title: medical NER model
client_industry: healthcare
date: 2024-12
skills:
- NLP
- named entity recognition
- Hugging Face Transformers
synthetic: true
---
# medical NER model

For a healthcare client (2024-08 to 2024-12). Coders manually tagged medications and dosages in discharge summaries.

Fine-tuned a biomedical BERT model on 4,800 annotated notes. Added rule-based post-processing for dosage units. Delivered a batch inference job with a confidence threshold for human review.

Outcome: entity F1: 0.87; manual tagging time: cut by 60%.
