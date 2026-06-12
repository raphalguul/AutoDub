"""
SRT parsing, serialization, validation, and normalization for wtd_preview.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class SubtitleEntry:
    index: int
    start: float
    end: float
    text: str
    is_dub: bool = False
    dub_type: Optional[str] = None
    dub_start: Optional[float] = None
    dub_end: Optional[float] = None


_TIMESTAMP_RE = re.compile(r'(\d+):(\d+):(\d+),(\d+)')
_DUB_RE = re.compile(r'\[(male|female)_dub\]', re.IGNORECASE)


def _parse_timestamp(ts: str) -> float:
    """Parse SRT timestamp (HH:MM:SS,mmm) to seconds."""
    m = _TIMESTAMP_RE.match(ts.strip())
    if not m:
        raise ValueError(f"Invalid timestamp: {ts}")
    h, mi, s, ms = map(int, m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def _format_timestamp(seconds: float) -> str:
    """Format seconds to SRT timestamp (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    int_secs = int(secs)
    milliseconds = int(round((secs - int_secs) * 1000))
    if milliseconds == 1000:
        milliseconds = 0
        int_secs += 1
        if int_secs == 60:
            int_secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{int_secs:02d},{milliseconds:03d}"


def parse_srt(content: str) -> List[SubtitleEntry]:
    """Parse SRT content into list of SubtitleEntry objects."""
    entries = []
    blocks = content.strip().split('\n\n')
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        
        # Parse timestamp line
        ts_match = re.match(r'(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)', lines[1])
        if not ts_match:
            continue
        start = _parse_timestamp(ts_match.group(1))
        end = _parse_timestamp(ts_match.group(2))
        
        text = '\n'.join(lines[2:]).strip()
        
        # Detect dub marker
        is_dub = False
        dub_type = None
        dub_match = _DUB_RE.search(text)
        if dub_match:
            is_dub = True
            dub_type = dub_match.group(1).lower()
        
        entries.append(SubtitleEntry(
            index=index,
            start=start,
            end=end,
            text=text,
            is_dub=is_dub,
            dub_type=dub_type
        ))
    
    return entries


def serialize_srt(entries: List[SubtitleEntry]) -> str:
    """Serialize SubtitleEntry objects back to SRT format."""
    lines = []
    for entry in entries:
        lines.append(str(entry.index))
        lines.append(f"{_format_timestamp(entry.start)} --> {_format_timestamp(entry.end)}")
        lines.append(entry.text)
        lines.append('')  # Empty line between entries
    return '\n'.join(lines).strip() + '\n'


def normalize_dub_markers(content: str) -> Tuple[str, bool]:
    """Normalize dub marker case and spacing. Returns (fixed_content, changed)."""
    changed = False
    
    def replace_dub(match):
        nonlocal changed
        inner = match.group(1).lower()
        if match.group(0) != f'[{inner}_dub]':
            changed = True
        return f'[{inner}_dub]'
    
    fixed = _DUB_RE.sub(replace_dub, content)
    return fixed, changed


def validate_single_dub(content: str) -> Tuple[bool, str]:
    """Validate that exactly one dub marker exists in entire SRT."""
    entries = parse_srt(content)
    dub_count = sum(1 for e in entries if e.is_dub)
    
    if dub_count == 0:
        return False, "No dub marker found in SRT. Preview requires exactly one dub."
    if dub_count > 1:
        return False, f"Multiple dub markers found ({dub_count}). Preview requires exactly one dub."
    
    # Validate the single dub entry has dub_start/dub_end potential
    for entry in entries:
        if entry.is_dub:
            if entry.start >= entry.end:
                return False, f"Dub line {entry.index} has invalid timestamps."
            break
    
    return True, ""


def infer_dub_boundaries(entry: SubtitleEntry) -> Tuple[float, float]:
    """Infer dub start/end within a line. Defaults to full line if not set."""
    if entry.dub_start is not None and entry.dub_end is not None:
        return entry.dub_start, entry.dub_end
    # Default: dub spans the entire line
    return entry.start, entry.end