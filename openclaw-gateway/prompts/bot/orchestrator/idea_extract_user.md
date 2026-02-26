Extract only the project idea text from the user message.
Return JSON only with this schema:
{{"idea_text": "...", "confidence": 0.0}}
If idea text is unclear, return empty idea_text and low confidence.

User message: {user_message}
