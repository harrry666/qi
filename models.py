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
        self.trial_ends_at = row.get('trial_ends_at')
        self.subscription_status = row.get('subscription_status', 'none')
        self.stripe_customer_id = row.get('stripe_customer_id')
        self.stripe_subscription_id = row.get('stripe_subscription_id')

    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False

    def get_id(self): return str(self.id)
