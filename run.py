import os
import sys
from app import create_app

app = create_app()

if __name__ == '__main__':
    if '--init-db' in sys.argv:
        with app.app_context():
            from app.db import init_db
            init_db()
            print('\n  Database initialized.\n')
    else:
        port = int(os.environ.get('PORT', 5000))
        print(f'\n  QC Monitor running at http://localhost:{port}\n')
        app.run(debug=True, host='0.0.0.0', port=port)
