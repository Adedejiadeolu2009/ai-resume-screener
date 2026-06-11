import random
import resend
import os
from upstash_redis import Redis
from dotenv import load_dotenv

load_dotenv()

redis = Redis(
    url=os.getenv("UPSTASH_REDIS_REST_URL"),
    token=os.getenv("UPSTASH_REDIS_REST_TOKEN")
)

resend.api_key = os.getenv("RESEND_API_KEY")

def send_otp_email(email: str):
    code = str(random.randint(100000, 999999))
    redis.set(f"otp:{email}", code, ex=300)  # expires in 5 minutes
    
    resend.Emails.send({
        "from": os.getenv("EMAIL_FROM"),
        "to": email,
        "subject": "Your Aptura Verification Code",
        "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 400px; margin: auto;">
                <h2>Verify your identity</h2>
                <p>Use the code below to complete your verification:</p>
                <h1 style="letter-spacing: 6px; color: #4F46E5;">{code}</h1>
                <p>This code expires in <strong>5 minutes</strong>.</p>
                <p style="color: grey; font-size: 12px;">If you didn't request this, ignore this email.</p>
            </div>
        """
    })

def verify_otp(email: str, code: str) -> bool:
    stored = redis.get(f"otp:{email}")
    if not stored:
        return False  # expired or never sent
    if stored != code:
        return False  # wrong code
    redis.delete(f"otp:{email}")  # delete after use
    return True