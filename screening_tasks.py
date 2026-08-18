import json
import base64
import logging
import os
import time
import traceback

from celery_worker import celery_app
from database import SessionLocal
import models
from screen_router import extract_text, screen_with_openai

logger = logging.getLogger(__name__)


def _resolve_ai_key() -> str:
    return (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or os.environ.get("GROQ_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )


def process_screening(screening_id: int, files: list[dict]) -> dict:
    db = SessionLocal()
    processed = 0
    errors = 0

    try:
        screening = db.query(models.Screening).filter(
            models.Screening.id == screening_id
        ).first()
        if not screening:
            return {"screening_id": screening_id, "status": "NOT_FOUND"}

        screening.status = "PROCESSING"
        screening.error_message = None
        db.commit()

        job = screening.job
        api_key = _resolve_ai_key()
        if not api_key:
            raise RuntimeError("AI service is not configured.")

        for item in files:
            candidate = db.query(models.Candidate).filter(
                models.Candidate.id == item["candidate_id"],
                models.Candidate.screening_id == screening_id,
            ).first()
            if not candidate:
                continue

            candidate.status = "PROCESSING"
            candidate.error_message = None
            db.commit()

            try:
                if not candidate.file_content_b64:
                    raise ValueError("Queued resume content is missing.")

                file_bytes = base64.b64decode(candidate.file_content_b64)
                candidate_name = (
                    os.path.splitext(item["filename"])[0]
                    .replace("_", " ")
                    .replace("-", " ")
                    .title()
                )

                resume_text = extract_text(item["filename"], file_bytes)
                if len(resume_text.strip()) < 50:
                    raise ValueError("Could not extract enough text from this file.")

                result = screen_with_openai(
                    api_key,
                    job.description,
                    resume_text,
                    candidate_name,
                )
                result["filename"] = item["filename"]
                result["file_size_kb"] = round(len(file_bytes) / 1024, 1)

                candidate.candidate_name = result.get(
                    "candidate_name", candidate_name
                )
                candidate.overall_score = result.get("overall_score", 0)
                candidate.recommendation = result.get("recommendation", "")
                candidate.result_json = result
                candidate.status = "COMPLETED"
                candidate.error_message = None
                processed += 1
            except json.JSONDecodeError:
                errors += 1
                candidate.status = "FAILED"
                candidate.error_message = (
                    "AI returned an unexpected response. Please try again."
                )
            except Exception as exc:
                errors += 1
                candidate.status = "FAILED"
                candidate.error_message = f"Processing failed: {exc}"
                logger.error(
                    "Error processing candidate %s: %s",
                    candidate.id,
                    traceback.format_exc(),
                )
            finally:
                screening.processed_candidates = processed + errors
                screening.total_candidates = processed
                db.commit()

            delay = float(os.getenv("SCREENING_WORKER_DELAY_SECONDS", "0"))
            if delay > 0:
                time.sleep(delay)

        screening.status = "COMPLETED" if processed else "FAILED"
        if errors and processed:
            screening.status = "COMPLETED_WITH_ERRORS"
        if not processed and errors:
            screening.error_message = "All resumes failed to process."
        screening.total_candidates = processed
        screening.processed_candidates = processed + errors
        db.commit()

        return {
            "screening_id": screening_id,
            "status": screening.status,
            "processed": processed,
            "errors": errors,
        }
    except Exception as exc:
        db.rollback()
        screening = db.query(models.Screening).filter(
            models.Screening.id == screening_id
        ).first()
        if screening:
            screening.status = "FAILED"
            screening.error_message = str(exc)
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="screening.process_screening", bind=True, max_retries=2)
def process_screening_task(self, screening_id: int, files: list[dict]) -> dict:
    return process_screening(screening_id, files)
