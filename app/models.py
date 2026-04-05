from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, id, username, full_name, role, branch_id=None, email=None, branch_name=None):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.role = role
        self.branch_id = branch_id
        self.email = email
        self.branch_name = branch_name

    @property
    def is_it_admin(self):
        return self.role == 'it_admin'

    @property
    def is_qc_admin(self):
        return self.role == 'qc_admin'

    @property
    def is_branch_manager(self):
        return self.role == 'branch_manager'

    @property
    def is_management(self):
        return self.role == 'management'

    @property
    def role_label(self):
        labels = {
            'it_admin': 'IT Admin',
            'branch_manager': 'Branch Manager',
            'qc_admin': 'QC Admin',
            'management': 'Management',
        }
        return labels.get(self.role, self.role)

    @staticmethod
    def from_db_row(row):
        if row is None:
            return None
        return User(
            id=row['id'],
            username=row['username'],
            full_name=row['full_name'],
            role=row['role'],
            branch_id=row['branch_id'],
            email=row['email'],
            branch_name=row['branch_name'] if 'branch_name' in row.keys() else None,
        )
