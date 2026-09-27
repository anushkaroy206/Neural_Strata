"""Blackbox archive on a card mounted in the laptop, not on the hub.

The hub firmware still reports ``BLACKBOX:NOSD`` when its own SPI socket is
empty. Rows already arrive over USB. This writer copies each fresh row onto
whatever removable volume macOS has mounted, as the same chunk CSV the hub
would have written. A set ``NS_BBX_LAPTOP_VOLUME`` pins the folder; otherwise
the first writable directory under ``/Volumes`` that is not an internal
system volume is the card.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_SKIP = {
    "Macintosh HD", "Recovery", "Preboot", "VM", "Update", "Data",
}
_HEADER = (
    "chunk_id,seq,ts_ms,node,level,tilt_deg,disp_mm,accel_mps2,"
    "vib,micro_um,macro_mm,flags\n"
)


class LaptopCard:
    def __init__(self, root: Optional[str] = None, log=None):
        self._forced = root if root is not None else os.environ.get("NS_BBX_LAPTOP_VOLUME")
        self.log = log or (lambda *_: None)
        self._seen: Optional[str] = None
        self.rows = 0
        self._rows = 0
        # Highest chunk this process has appended. That file is the one still
        # growing; every lower id is an earlier chunk.
        self.writing: Optional[int] = None

    def volume(self) -> Optional[Path]:
        if self._forced:
            path = Path(self._forced)
            return path if path.is_dir() and os.access(path, os.W_OK) else None
        root = Path("/Volumes")
        if not root.is_dir():
            return None
        for child in sorted(root.iterdir()):
            if child.name in _SKIP or child.is_symlink() or not child.is_dir():
                continue
            if os.access(child, os.W_OK):
                return child
        return None

    def snapshot(self) -> dict:
        vol = self.volume()
        name = str(vol) if vol else None
        if name != self._seen:
            self._seen = name
            self.log(f"laptop card {name or 'absent'}")
        if vol is None:
            return {"mounted": False}
        st = os.statvfs(vol)
        total = int(st.f_blocks * st.f_frsize / (1024 * 1024))
        free = int(st.f_bavail * st.f_frsize / (1024 * 1024))
        files = self._chunk_files(vol)
        out = {"mounted": True, "path": name, "free_mb": free, "card_mb": total,
               "rows": self.rows, "on_card": len(files)}
        chunk_id = self.writing
        if chunk_id is None and files:
            try:
                chunk_id = int(files[-1].stem.split("_")[-1])
            except ValueError:
                chunk_id = None
        if chunk_id is not None:
            out["chunk_id"] = chunk_id
            if self.writing == chunk_id:
                out["row"] = self._rows
            else:
                out["row"] = self._count_rows(files[-1])
        return out

    def append(self, row) -> bool:
        vol = self.volume()
        if vol is None:
            return False
        folder = vol / "neural-strata-blackbox"
        folder.mkdir(exist_ok=True)
        path = folder / f"chunk_{int(row.chunk_id):04d}.csv"
        new = not path.exists() or path.stat().st_size == 0
        line = (
            f"{int(row.chunk_id)},{int(row.seq)},{int(row.ts_ms)},"
            f"{int(row.node)},{int(row.level)},{float(row.tilt):.2f},"
            f"{float(row.disp_mm):.1f},{float(row.accel):.3f},{int(row.vib)},"
            f"{float(row.micro):.1f},{float(row.macro):.1f},{int(row.flags)}\n"
        )
        with path.open("a") as fh:
            if new:
                fh.write(_HEADER)
            fh.write(line)
        cid = int(row.chunk_id)
        if self.writing is None or cid > self.writing:
            self.writing = cid
            self._rows = self._count_rows(path)
        elif cid == self.writing:
            self._rows += 1
        self.rows += 1
        return True

    def _chunk_files(self, vol: Path) -> list:
        folder = vol / "neural-strata-blackbox"
        if not folder.is_dir():
            return []
        return sorted(
            p for p in folder.glob("chunk_*.csv")
            if p.is_file() and not p.name.startswith(".")
        )

    def _count_rows(self, path: Path) -> int:
        try:
            with path.open() as fh:
                n = sum(1 for line in fh if line.strip())
        except OSError:
            return 0
        return max(0, n - 1)

    def chunk_path(self, chunk_id: int) -> Optional[Path]:
        vol = self.volume()
        if vol is None:
            return None
        return vol / "neural-strata-blackbox" / f"chunk_{int(chunk_id):04d}.csv"

    def release(self, chunk_id: int) -> str:
        """Unlink one archive file. ``removed`` only when a file was actually there.

        A missing file is already gone (``absent``). The open chunk the writer is
        still appending must never be passed in — the caller decides that.
        """
        path = self.chunk_path(chunk_id)
        if path is None:
            result = "no-card"
        elif not path.is_file():
            result = "absent"
        else:
            path.unlink()
            result = "removed" if not path.exists() else "failed"
        return result
