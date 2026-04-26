## Update api schema

npm run gen:types
# 1. Create virtual environment
python -m venv venv

# 2. Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install your dependencies
pip install fastapi unicorn sqlalchemy psycopg2-binary

# 4. Generate requirements.txt
pip freeze > requirements.txt

# 5. Share your project - others can install with:
pip install -r requirements.txt