@router.get("/{server_id}/backup-progress", response_class=JSONResponse)
async def get_backup_progress(
    server_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user)
):
    server = session.get(Server, server_id)
    if not server:
        raise HTTPException(404)

    # Prefer DB-backed Job status (worker feeds this)
    latest_job = session.exec(
        select(Job)
        .where(Job.server_id == server.id, Job.job_type == "backup")
        .order_by(Job.started_at.desc())
        .limit(1)
    ).first()

    prog = backup_svc.get_backup_progress(server.hostname)  # Redis fallback for live lines

    data = {
        "current": prog.get("current"),
        "log_lines": prog.get("log_lines", [])[-15:],
        "last_updated": prog.get("last_updated"),
        "hostname": server.hostname
    }

    if latest_job:
        data["job_status"] = latest_job.status
        try:
            if latest_job.details:
                job_details = json.loads(latest_job.details)
                if job_details.get("current"):
                    data["current"] = job_details["current"]
        except Exception:
            pass

    return data