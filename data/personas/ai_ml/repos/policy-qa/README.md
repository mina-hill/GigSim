# policy-qa

clinical policy chatbot: support staff spent hours searching 3,400 pages of internal policy PDFs and often quoted outdated versions.

## What it does

- Chunked PDFs by section with page numbers and indexed them in pgvector with hybrid keyword search
- Answers cite the page and refuse when no passage supports them
- Built a 200-question evaluation set with the support lead and ran it on every change

## Results

- citation accuracy: 91% on 200 labelled questions
- correct refusals on out-of-scope questions: 96%

_Synthetic repository for the GigBid project._
