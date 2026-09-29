"""Create or verify a consistent SQLite snapshot without starting the app."""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def verify(path: Path) -> None:
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('SQLite integrity check failed')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('SQLite foreign-key check failed')
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'activities' not in tables:
            raise RuntimeError('The snapshot does not contain the Runstead activities table')


def backup(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError('Choose a new snapshot path; existing backups are preserved')
    if source.resolve() == destination.resolve():
        raise ValueError('The source and snapshot must be different files')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    if temporary.exists():
        raise FileExistsError('A partial snapshot already exists; choose a new path')
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
        verify(temporary)
        temporary.rename(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path, nargs='?')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        verify(args.source)
    else:
        if args.destination is None:
            parser.error('A destination is required for backup')
        backup(args.source, args.destination)
    print('Runstead SQLite snapshot verified.')
