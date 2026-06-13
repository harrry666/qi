class Business:
    def __init__(self, row):
        self.id = row['id']
        self.name = row['name']
        self.slug = row['slug']
        self.email = row['email']
        self.address = row['address']
        self.phone = row['phone']
        self.description = row['description']
        self.category = row.get('category', '')
        self.avatar_url = row.get('avatar_url', '')
        self.cover_url = row.get('cover_url', '')

    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False

    def get_id(self): return str(self.id)
