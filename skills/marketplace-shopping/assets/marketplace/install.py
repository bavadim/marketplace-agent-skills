#!/usr/bin/env python3
"""Install the two sibling skills and archive earlier versions without deleting them."""
import argparse,datetime,os,shutil,uuid
from pathlib import Path


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--destination',type=Path,help='Override installed skill root (for isolated validation)')
 parser.add_argument('--backup-root',type=Path,help='Override recoverable backup root')
 args=parser.parse_args()
 source=Path(__file__).resolve().parents[3]
 target=args.destination or Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills'
 target.mkdir(parents=True,exist_ok=True)
 names=('marketplace-shopping','marketplace-orders')
 for name in names:
  if not (source/name/'SKILL.md').is_file():raise SystemExit('Both source skills are required.')
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
 backup=(args.backup_root or Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'marketplace-shopping/skill-backups')/stamp
 backup.mkdir(parents=True,mode=0o700)
 staged={};moved=[];installed=[]
 try:
  for name in names:
   stage=target/('.stage-'+name+'-'+stamp)
   shutil.copytree(source/name,stage,ignore=shutil.ignore_patterns('__pycache__','*.pyc'));staged[name]=stage
  for name in ('aliexpress-bom','ozon-bom','delivery-agenda','marketplace-orders','marketplace-shopping'):
   current=target/name
   if current.exists():current.rename(backup/name);moved.append(name)
  for name,stage in staged.items():stage.rename(target/name);installed.append(name)
  for name in names:
   for file in (source/name).rglob('*'):
    if file.is_file() and '__pycache__' not in file.parts:
     if file.read_bytes()!=(target/name/file.relative_to(source/name)).read_bytes():raise RuntimeError('Installed file differs.')
 except BaseException:
  for name in installed:(target/name).rename(backup/('failed-new-'+name))
  for name in moved:(backup/name).rename(target/name)
  raise
 print('Installed: '+', '.join(names))
 print('Recoverable previous skills: '+str(backup))

if __name__=='__main__':main()
