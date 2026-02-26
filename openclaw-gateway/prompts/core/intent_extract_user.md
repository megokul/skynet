Extract user intent for a multi-role agent orchestrator.
Return ONLY valid JSON with this schema:
{{"intent":"...","confidence":0.0,"entities":{{}},"recommended_role":null}}
Allowed roles: igris, project_specialist, coding_specialist, weather_specialist, reminder_specialist, research_specialist.
If uncertain, set intent='exploratory' and recommended_role='igris'.
Active role: {active_role}
Active project id: {active_project_id}
User message: {user_message}
