# Contributing to MATE

Thank you for your interest in contributing to MATE (Multi-Agent Tree Engine)! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- Python 3.8+
- Git
- A database backend (SQLite for development, PostgreSQL/MySQL for production)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/antiv/mate.git
cd mate

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template and configure
cp .env.example .env
# Edit .env with your API keys and database settings

# Run the server
python auth_server.py
```

### Running Tests

```bash
# Activate virtual environment first
source .venv/bin/activate

# Run all tests
python -m unittest discover -s shared/test -p "test_*.py" -v

# Run a specific test module
python -m unittest shared.test.test_agent_manager_simple -v
```

## How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/antiv/mate/issues) to avoid duplicates
2. Open a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Python version, OS, and database type
   - Relevant logs (with sensitive data redacted)

### Suggesting Features

Open an issue with the `enhancement` label. Include:
- Use case description
- Proposed solution
- Alternatives you've considered

### Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes following the code style below
4. Add or update tests for your changes
5. Run the test suite to make sure everything passes
6. Commit with clear messages: `git commit -m "Add support for X"`
7. Push to your fork: `git push origin feature/your-feature-name`
8. Open a Pull Request against `main`

## Code Style

- **Python 3.8+** for all new files
- **PEP 8** with 4-space indentation
- **Type hints** for function parameters and return types
- **f-strings** for string formatting
- **Imports** grouped: stdlib, third-party, local (separated by blank lines)
- Use `logger` instead of `print()` for all output
- Prefer descriptive variable names over comments

### Architecture Patterns

- Follow the **Agent-Manager-Tool Factory** pattern
- Keep database models in `shared/utils/models.py`
- Keep agent business logic separate from database operations
- Tools should be created through the `ToolFactory` system
- All agents must include token usage tracking callbacks

### Database Changes

- All schema changes must go through the migration system
- Create migrations for each DB type: `shared/sql/migrations/{postgresql,mysql,sqlite}/`
- Migrations run automatically on server startup
- Use `python shared/migrate.py create <name>` to scaffold a new migration

### Testing Requirements

- Write tests for all new functionality
- Test both success and failure scenarios
- Use mocking for external API calls
- Place test files in `shared/test/` with `test_` prefix
- Always run tests as modules: `python -m unittest ...`

## Project Structure

Key directories:

- `agents/` - Agent implementations (each in its own subdirectory)
- `shared/utils/` - Core utilities (AgentManager, DatabaseClient, ToolFactory)
- `shared/utils/tools/` - Tool implementations
- `shared/utils/mcp/` - MCP server implementations
- `shared/callbacks/` - System callbacks (RBAC, token tracking, guardrails)
- `shared/sql/migrations/` - Database migrations (per DB type)
- `shared/test/` - Test suite
- `templates/` - Dashboard HTML templates
- `static/` - Frontend assets (CSS, JS)
- `documents/` - Feature documentation

## Security

- Never commit API keys, tokens, or credentials
- Use environment variables for all sensitive configuration
- Never log sensitive information (API keys, tokens, passwords)
- Report security vulnerabilities privately (see [SECURITY.md](SECURITY.md))

## License

By contributing to MATE, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
