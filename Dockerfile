FROM python:3.12-slim

WORKDIR /app

COPY agents_meeting_prep/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY agents_meeting_prep ./agents_meeting_prep

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV MEETING_PREP_AGENT_MODE=in_process

EXPOSE 8080

CMD ["python", "-m", "agents_meeting_prep.mcp_server"]
