"""The Uploader Agreement: what a creator promises about a video they upload, and what CineCut promises about handling
it. It is shown in full before the first upload, accepted per file, and recorded with the file's fingerprint, so that
later either side can show exactly what was agreed about exactly which file (backend.webapp.db.record_consent).

The text is versioned: a change to VERSION or to TEXT changes text_hash(), and an acceptance carries the hash it was
given, so an old acceptance can never be read as agreement to newer wording. Written in plain English on purpose -
the people uploading are creators and teachers, not lawyers. It is not legal advice, and a lawyer should read it
before real customers sign it.
"""
import hashlib
import textwrap
from typing import Any, Dict

VERSION = "2026-09-18"

# What the uploader says about their right to the file. The wording is shown next to the choice and is recorded.
BASES: Dict[str, str] = {
    "own": "I made this recording myself, or I own its rights.",
    "permission": "The owner has given me written permission to use it this way.",
    "open": "It is public domain, or under an open licence that allows this.",
}
# The extra line we ask for when the right comes from somebody else.
BASIS_DETAIL: Dict[str, str] = {
    "own": "",
    "permission": "Who gave the permission, and when",
    "open": "Which licence, and where the file came from",
}

TEXT = """CineCut Uploader Agreement

When you upload a video, you are asking CineCut to make a shorter version of it for you. This says what you promise about the file, and what we promise about handling it.

1. The file is yours to give.
   One of these is true, and you tell us which one when you upload: you made the recording yourself; or you own its rights; or the owner has given you written permission to use it this way; or it is public domain or under an open licence that allows this. Your answer is kept with the file's fingerprint.

2. What the file must not contain.
   Someone else's film, television, music or stock footage that you hold no licence for. Anything unlawful in India or where you live. A person filmed in a private setting who has not agreed to this. A child shown unsafely. Anything made to mislead people about a real person or a real event.

3. What we do with the file.
   Only what is needed to make what you asked for: store it, convert it, transcribe the speech, translate it, shorten it, and produce your files. We do not publish your video, do not sell it, do not use it to train AI models, and no other user of CineCut can see it.

4. What comes back is made with AI.
   The narration, the voices, the captions and the summaries are generated, and they can be wrong. Every file we make carries a card saying it was made with AI. Watch the result before you use it anywhere.

5. Where you publish it is your decision.
   YouTube, Instagram and other platforms have their own rules, including rules about AI-made content and about other people's material. Following them is your responsibility.

6. Keeping and deleting.
   Your upload and the files made from it stay until you delete them. Delete removes the uploaded file and everything made from it. This agreement record itself is kept, so that both sides can show what was agreed.

7. If somebody makes a claim.
   If anyone claims you had no right to what you uploaded, answering that claim is your responsibility, as is any cost that falls on CineCut because a promise here was not kept. If we are told an upload breaks someone's rights, we may remove it, and we will tell you why.

8. Where this sits.
   This agreement sits under the CineCut Terms of Use and the privacy notice in them. Where they differ about your own uploads, this agreement is the one that counts.

When you tick the box, we record the date and time, your account, which of the promises in point 1 you chose, the file's name and size, and a SHA-256 fingerprint of its contents, so that the record points at that exact file."""


def wrapped(width: int = 92) -> str:
    """The same wording laid out for a plain-text file; on screen the page wraps it to the panel instead."""
    out = []
    for para in TEXT.split("\n"):
        indent = " " * (len(para) - len(para.lstrip()))
        out.extend(textwrap.wrap(para.strip(), width, initial_indent=indent, subsequent_indent=indent) if para.strip() else [""])
    return "\n".join(out)


# Shortening a link: nothing is kept. Accepted per request, recorded with the link (no file is ever kept to fingerprint).
SHORTEN_VERSION = "2026-09-18"
SHORTEN_TEXT = """Shortening a video with CineCut

1. You give us a link to a video you are allowed to watch.
2. CineCut fetches the video only to make the short version. The original is deleted as soon as the short version is ready.
3. The short version is shown only to you, on this page. It cannot be downloaded or saved. It is deleted 60 minutes after you last watch it, when you press Delete, or when CineCut restarts.
4. Nothing from the video is kept: no copy, no clips, no pictures. We keep only this record: your account, the link, the time and the choices you made.
5. Links to paid streaming services (Netflix, Prime Video and the like) are refused. They are protected, and we do not copy them.
6. The narration and the choice of scenes are made with AI, and can be wrong.
7. The short version is for your own viewing. Do not record it or share it."""


def shorten_hash() -> str:
    return hashlib.sha256(f"{SHORTEN_VERSION}\n{SHORTEN_TEXT}".encode("utf-8")).hexdigest()


def text_hash() -> str:
    """Fingerprint of this exact wording; an acceptance must carry it, so it cannot be moved to different wording."""
    return hashlib.sha256(f"{VERSION}\n{TEXT}".encode("utf-8")).hexdigest()


def summary() -> Dict[str, Any]:
    """What the upload page shows and sends back."""
    return {"version": VERSION, "hash": text_hash(), "text": TEXT, "bases": BASES, "details": BASIS_DETAIL}


def receipt(row: Dict[str, Any]) -> str:
    """The user's copy of one acceptance: what was agreed, about which file, when."""
    size = f"{(row.get('file_bytes') or 0) / 2 ** 20:.1f} MB"
    when = row.get("created_text") or ""
    lines = [
        "CINECUT UPLOADER AGREEMENT - RECORD OF ACCEPTANCE",
        "",
        f"Agreement version   {row.get('version')}",
        f"Wording fingerprint {row.get('text_hash')}",
        f"Accepted            {when}",
        f"Account             {row.get('email') or ''} (user {row.get('user_id')})",
        f"From                {row.get('ip') or 'unknown address'}",
        f"Browser             {(row.get('agent') or '')[:120]}",
        "",
        f"File                {row.get('file_name')}",
        f"Size                {size}",
        f"SHA-256             {row.get('file_sha256')}",
        f"Upload reference    {row.get('work_id') or ''}",
        "",
        f"Declared            {BASES.get(row.get('basis') or '', row.get('basis') or '')}",
    ]
    if row.get("details"):
        lines.append(f"Details             {row['details']}")
    return "\n".join(lines) + "\n\n" + "-" * 78 + "\n\nThe wording accepted:\n\n" + wrapped() + "\n"
