from __future__ import annotations

from api.api.session_attachments import delete_session_attachment_files


def delete_session_data(db, session_id: str) -> bool:
    row = db.execute(
        "SELECT id FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return False

    delete_session_attachment_files(db, session_id)

    db.execute(
        "DELETE FROM plan_revisions WHERE plan_id IN (SELECT id FROM plans WHERE session_id = ?)",
        (session_id,),
    )
    db.execute("DELETE FROM behavior_feedback_events WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM behavior_proposal_dismissals WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM plan_proposals WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM behavior_patches WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM workspace_evidence WHERE session_id = ?", (session_id,))
    db.execute(
        "DELETE FROM plan_items WHERE plan_id IN (SELECT id FROM plans WHERE session_id = ?)",
        (session_id,),
    )
    db.execute("DELETE FROM session_plan_state WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM plans WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM session_plans WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM agent_events WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM tool_invocations WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM captures WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM session_attachments WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()
    return True