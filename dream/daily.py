"""Hermes --no-agent entrypoint: emit one owner's report to stdout."""
import sys

from dream.proposals import daily_proposal
from dream.server import from_env


def main():
    try:
        report = daily_proposal(from_env(force_readonly=True))
    except (OSError, ValueError, KeyError):
        print('Dream: не удалось сформировать сводку. Проверь конфигурацию задания.', file=sys.stderr)
        return 1
    if report['message']:
        print(report['message'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
