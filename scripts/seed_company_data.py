import argparse
from pathlib import Path

from app.database.session import create_session_factory
from app.services.company_seed import load_company_seed, seed_company_evidence

DEFAULT_SEED_PATH = Path("data/company/seed/test_company.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed company evidence data ")
    
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_SEED_PATH,
        help="Path to the consolidated company evidence JSON file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    seed = load_company_seed(args.path)
    
    session_factory = create_session_factory()

    # Commit all evidence together, or roll back the complete seed on failure
    with session_factory() as session, session.begin():
        record_count = seed_company_evidence(session, seed) #upsert 

    print(f"Seeded {record_count} company evidence records")


if __name__ == "__main__":
    main()
