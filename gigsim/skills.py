"""How GigSim's simulated clients recognise a skill being mentioned in a proposal.

Clients look for any alias as a whole word or phrase. This is the simulator's own notion of
"mentioned", deliberately independent of GigBid's skill taxonomy.
"""

from __future__ import annotations

import re
from functools import lru_cache

ALIASES: dict[str, list[str]] = {
    "python": ["python", "pandas", "numpy", "fastapi", "django", "flask"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "pytorch": ["pytorch", "torch"],
    "tensorflow": ["tensorflow", "keras"],
    "machine learning": ["machine learning", "ml model", "ml models", "classifier", "regression"],
    "deep learning": ["deep learning", "neural network", "neural networks", "cnn", "transformer"],
    "nlp": ["nlp", "natural language", "text classification", "named entity", "llm", "llms"],
    "llm": ["llm", "llms", "gpt", "rag", "langchain", "langgraph", "openai", "prompt"],
    "rag": ["rag", "retrieval", "vector database", "embeddings", "pinecone", "pgvector"],
    "computer vision": [
        "computer vision", "opencv", "image classification", "object detection", "yolo",
    ],
    "mlops": ["mlops", "mlflow", "model deployment", "model serving", "docker"],
    "javascript": ["javascript", "js", "node", "node.js"],
    "typescript": ["typescript", "ts"],
    "react": ["react", "react.js", "reactjs", "next.js", "nextjs"],
    "next.js": ["next.js", "nextjs"],
    "node.js": ["node.js", "nodejs", "node", "express"],
    "postgresql": ["postgresql", "postgres", "psql"],
    "mongodb": ["mongodb", "mongo"],
    "api development": ["api", "apis", "rest", "graphql", "endpoint", "endpoints"],
    "stripe": ["stripe", "payments integration", "checkout"],
    "aws": ["aws", "amazon web services", "lambda", "s3", "ec2"],
    "docker": ["docker", "container", "containers", "docker compose"],
    "tailwind css": ["tailwind", "tailwind css"],
    "sql": ["sql", "postgres", "mysql", "bigquery", "snowflake", "query", "queries"],
    "excel": ["excel", "spreadsheet", "spreadsheets", "pivot table", "pivot tables", "vlookup"],
    "power bi": ["power bi", "powerbi", "dax"],
    "tableau": ["tableau"],
    "data visualization": [
        "data visualization", "dashboard", "dashboards", "charts", "visualisation",
    ],
    "dbt": ["dbt"],
    "airflow": ["airflow", "dag", "dags"],
    "statistics": ["statistics", "statistical", "a/b test", "hypothesis test", "regression"],
    "etl": ["etl", "elt", "pipeline", "pipelines", "data pipeline"],
    "google sheets": ["google sheets", "sheets", "apps script"],
    "web scraping": ["scraping", "scraper", "crawler"],
}


@lru_cache(maxsize=4096)
def _pattern(alias: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])")


def mentions(text: str, skill: str) -> bool:
    lowered = text.lower()
    aliases = ALIASES.get(skill.lower(), [skill.lower()])
    return any(_pattern(a).search(lowered) for a in aliases)
