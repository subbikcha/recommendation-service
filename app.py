from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from collections import Counter
import requests

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = \
    'postgresql://ripple:ripple@localhost:5432/ripple'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ── Models ────────────────────────────────────────────────────────────────────

class Restaurant(db.Model):
    __tablename__ = 'restaurants'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    cuisine_type = db.Column(db.String(50))
    rating = db.Column(db.Float, default=4.0)
    is_premium_only = db.Column(db.Boolean, default=False)
    avg_delivery_time = db.Column(db.Integer, default=30)
    base_price = db.Column(db.Integer, default=200)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'cuisineType': self.cuisine_type,
            'rating': self.rating,
            'isPremiumOnly': self.is_premium_only,
            'avgDeliveryTime': self.avg_delivery_time,
            'basePrice': self.base_price,
            'isActive': self.is_active,
        }


class Recommendation(db.Model):
    __tablename__ = 'recommendations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50))
    user_name = db.Column(db.String(100))
    tier = db.Column(db.String(50))

    # From user-service: walletBalance, address (city), phoneNumber
    user_wallet_balance = db.Column(db.Float)
    user_city = db.Column(db.String(100))
    user_phone = db.Column(db.String(50))
    wallet_unlocked_premium = db.Column(db.Boolean, default=False)

    # From order-service: dominant cuisine from item history, avg spend
    dominant_cuisine_from_orders = db.Column(db.String(50))
    avg_spend_from_orders = db.Column(db.Integer)
    delivered_order_count = db.Column(db.Integer, default=0)

    recommended_restaurant = db.Column(db.String(100))
    boost_score = db.Column(db.Integer)
    rating = db.Column(db.Float)
    cuisine_type = db.Column(db.String(50))
    delivery_time = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user_name,
            'tier': self.tier,
            'userWalletBalance': self.user_wallet_balance,
            'userCity': self.user_city,
            'userPhone': self.user_phone,
            'walletUnlockedPremium': self.wallet_unlocked_premium,
            'dominantCuisineFromOrders': self.dominant_cuisine_from_orders,
            'avgSpendFromOrders': self.avg_spend_from_orders,
            'deliveredOrderCount': self.delivered_order_count,
            'restaurant': self.recommended_restaurant,
            'boostScore': self.boost_score,
            'rating': self.rating,
            'cuisineType': self.cuisine_type,
            'deliveryTime': self.delivery_time,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
        }


class UserPreference(db.Model):
    __tablename__ = 'user_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), unique=True, nullable=False)
    preferred_cuisine = db.Column(db.String(50))
    avg_spend = db.Column(db.Integer, default=0)
    total_orders = db.Column(db.Integer, default=0)
    delivered_orders = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'preferredCuisine': self.preferred_cuisine,
            'avgSpend': self.avg_spend,
            'totalOrders': self.total_orders,
            'deliveredOrders': self.delivered_orders,
            'lastUpdated': self.last_updated.isoformat() if self.last_updated else None,
        }


# ── Seed Data ─────────────────────────────────────────────────────────────────

SEED_RESTAURANTS = [
    ('Pizza Palace',   'PIZZA',   4.5, False, 30, 250),
    ('Burger Barn',    'BURGER',  4.2, False, 20, 180),
    ('Sushi Supreme',  'SUSHI',   4.8, True,  45, 600),
    ('Spice Garden',   'INDIAN',  4.6, False, 35, 300),
    ('Dragon Wok',     'CHINESE', 4.3, False, 25, 220),
    ('Pasta Prima',    'ITALIAN', 4.7, True,  40, 500),
    ('Grill House',    'BURGER',  4.4, False, 25, 200),
    ('Curry Corner',   'INDIAN',  4.1, False, 30, 180),
]


def seed_restaurants():
    if Restaurant.query.count() == 0:
        for name, cuisine, rating, premium, delivery, price in SEED_RESTAURANTS:
            db.session.add(Restaurant(
                name=name, cuisine_type=cuisine, rating=rating,
                is_premium_only=premium, avg_delivery_time=delivery, base_price=price
            ))
        db.session.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_city(address):
    """Best-effort: last comma-separated part of address, stripped."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(',')]
    return parts[-1] if parts else None


def _fetch_user(user_id):
    """Returns user dict using: userId, userName, tier, rewardPoints,
       email, phoneNumber, address, walletBalance, isActive."""
    resp = requests.get(f'http://localhost:8081/users/{user_id}', timeout=3)
    resp.raise_for_status()
    return resp.json()


def _fetch_order_insights(user_id):
    """Call order-service, analyse items for dominant cuisine and avg spend.
    Returns: (dominant_cuisine, avg_spend, delivered_count, total_count)
    Uses: restaurantName, status, items[].category, finalAmount from each order.
    """
    try:
        resp = requests.get(f'http://localhost:8082/orders/user/{user_id}', timeout=3)
        if not resp.ok:
            return None, 0, 0, 0
        orders = resp.json()
        if not orders:
            return None, 0, 0, 0

        delivered = [o for o in orders if o.get('status') == 'DELIVERED']
        total_count = len(orders)
        delivered_count = len(delivered)

        # Dominant cuisine from item categories across delivered orders
        all_categories = []
        total_spend = 0
        for o in delivered:
            for item in o.get('items', []):
                cat = item.get('category')
                if cat:
                    all_categories.extend([cat] * item.get('quantity', 1))
            total_spend += o.get('finalAmount', 0) or 0

        dominant = Counter(all_categories).most_common(1)[0][0] if all_categories else None

        # Map item category → cuisine type used by restaurants
        category_to_cuisine = {
            'PIZZA': 'PIZZA', 'BURGER': 'BURGER', 'SUSHI': 'SUSHI',
            'INDIAN': 'INDIAN', 'CHINESE': 'CHINESE', 'ITALIAN': 'ITALIAN',
            'DRINK': None, 'DESSERT': None,
        }
        dominant_cuisine = category_to_cuisine.get(dominant) if dominant else None

        avg_spend = (total_spend // delivered_count) if delivered_count > 0 else 0
        return dominant_cuisine, avg_spend, delivered_count, total_count

    except Exception:
        return None, 0, 0, 0


# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'recommendation-service'})


# ── Restaurants ───────────────────────────────────────────────────────────────

@app.route('/restaurants', methods=['GET'])
def list_restaurants():
    cuisine = request.args.get('cuisine_type')
    query = Restaurant.query.filter_by(is_active=True)
    if cuisine:
        query = query.filter_by(cuisine_type=cuisine.upper())
    return jsonify([r.to_dict() for r in query.all()])


@app.route('/restaurants/<int:restaurant_id>', methods=['GET'])
def get_restaurant(restaurant_id):
    r = Restaurant.query.get(restaurant_id)
    if not r:
        return jsonify({'message': 'Restaurant not found'}), 404
    return jsonify(r.to_dict())


@app.route('/restaurants', methods=['POST'])
def create_restaurant():
    data = request.get_json()
    r = Restaurant(
        name=data['name'],
        cuisine_type=data.get('cuisine_type', 'OTHER'),
        rating=data.get('rating', 4.0),
        is_premium_only=data.get('is_premium_only', False),
        avg_delivery_time=data.get('avg_delivery_time', 30),
        base_price=data.get('base_price', 200),
    )
    db.session.add(r)
    db.session.commit()
    return jsonify(r.to_dict()), 201


# ── Recommendations ───────────────────────────────────────────────────────────

@app.route('/recommendations', methods=['GET'])
def list_recommendations():
    user_id = request.args.get('user_id')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))

    query = Recommendation.query.filter_by(is_active=True).order_by(Recommendation.created_at.desc())
    if user_id:
        query = query.filter_by(user_id=user_id)

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'items': [r.to_dict() for r in paginated.items],
        'total': paginated.total,
        'page': page,
        'pages': paginated.pages,
    })


@app.route('/recommendations/<int:rec_id>', methods=['GET'])
def get_recommendation(rec_id):
    r = Recommendation.query.get(rec_id)
    if not r:
        return jsonify({'message': 'Not found'}), 404
    return jsonify(r.to_dict())


@app.route('/recommendations/user/<user_id>', methods=['GET'])
def get_recommendations_for_user(user_id):
    recs = Recommendation.query.filter_by(user_id=user_id, is_active=True) \
        .order_by(Recommendation.created_at.desc()).all()
    return jsonify([r.to_dict() for r in recs])


@app.route('/recommendations/generate/<user_id>', methods=['POST'])
def generate_recommendation(user_id):

    # ── Step 1: Fetch user — uses userId, userName, tier, rewardPoints,
    #            email, phoneNumber, address, walletBalance, isActive ──────────
    try:
        user = _fetch_user(user_id)
    except Exception:
        return jsonify({'message': 'Could not fetch user from user-service'}), 503

    if not (user.get('isActive') if user.get('isActive') is not None else True):
        return jsonify({'message': 'User account is inactive'}), 403

    tier            = user.get('tier', 'new')
    reward_points   = user.get('rewardPoints', 0) or 0
    wallet_balance  = user.get('walletBalance', 0.0) or 0.0
    user_address    = user.get('address', '')
    user_phone      = user.get('phoneNumber', '')
    user_city       = _extract_city(user_address)

    # ── Step 2: Wallet check — walletBalance ≥ 500 unlocks premium restaurants ─
    wallet_unlocked_premium = wallet_balance >= 500.0

    # ── Step 3: Fetch order insights from order-service ─────────────────────
    #   Uses: status, items[].category, items[].quantity, finalAmount
    dominant_cuisine, avg_spend, delivered_count, total_count = _fetch_order_insights(user_id)

    # ── Step 4: Compute boost score from tier + reward points ─────────────────
    if tier == 'premium':
        boost_score = 10 + (reward_points // 100)
    elif tier == 'standard':
        boost_score = 5 + (reward_points // 2000)
    else:
        boost_score = 2

    # ── Step 5: Determine eligible restaurants ────────────────────────────────
    if tier == 'premium' or wallet_unlocked_premium:
        restaurants = Restaurant.query.filter_by(is_active=True).all()
    else:
        restaurants = Restaurant.query.filter_by(is_active=True, is_premium_only=False).all()

    if not restaurants:
        return jsonify({'message': 'No restaurants available'}), 404

    # ── Step 6: Rank by: dominant cuisine match > rating ──────────────────────
    def score(r):
        cuisine_match = 1.5 if (dominant_cuisine and r.cuisine_type == dominant_cuisine) else 0
        return r.rating + cuisine_match

    best = max(restaurants, key=score)

    # ── Step 7: Update user preference ────────────────────────────────────────
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    inferred_cuisine = dominant_cuisine or (pref.preferred_cuisine if pref else None)
    if pref:
        pref.total_orders      = total_count
        pref.delivered_orders  = delivered_count
        pref.avg_spend         = avg_spend
        if dominant_cuisine:
            pref.preferred_cuisine = dominant_cuisine
        pref.last_updated = datetime.utcnow()
    else:
        pref = UserPreference(
            user_id=user_id,
            preferred_cuisine=dominant_cuisine,
            avg_spend=avg_spend,
            total_orders=total_count,
            delivered_orders=delivered_count,
        )
        db.session.add(pref)

    # ── Step 8: Save recommendation ───────────────────────────────────────────
    rec = Recommendation(
        user_id=user.get('userId', user_id),
        user_name=user.get('userName', ''),
        tier=tier,
        user_wallet_balance=wallet_balance,
        user_city=user_city,
        user_phone=user_phone,
        wallet_unlocked_premium=wallet_unlocked_premium,
        dominant_cuisine_from_orders=dominant_cuisine,
        avg_spend_from_orders=avg_spend,
        delivered_order_count=delivered_count,
        recommended_restaurant=best.name,
        boost_score=boost_score,
        rating=best.rating,
        cuisine_type=best.cuisine_type,
        delivery_time=best.avg_delivery_time,
    )
    db.session.add(rec)
    db.session.commit()
    return jsonify(rec.to_dict()), 201


@app.route('/recommendations/<int:rec_id>', methods=['DELETE'])
def delete_recommendation(rec_id):
    r = Recommendation.query.get(rec_id)
    if not r:
        return jsonify({'message': 'Not found'}), 404
    r.is_active = False
    db.session.commit()
    return jsonify({'message': 'Deleted'})


# Backward-compat
@app.route('/recommendations/<user_id>', methods=['POST'])
def generate_recommendation_legacy(user_id):
    return generate_recommendation(user_id)


# ── Preferences ───────────────────────────────────────────────────────────────

@app.route('/preferences/<user_id>', methods=['GET'])
def get_preference(user_id):
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        return jsonify({'message': 'No preference set'}), 404
    return jsonify(pref.to_dict())


@app.route('/preferences/<user_id>', methods=['POST'])
def upsert_preference(user_id):
    data = request.get_json()
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if pref:
        pref.preferred_cuisine = data.get('preferred_cuisine', pref.preferred_cuisine)
        pref.avg_spend         = data.get('avg_spend', pref.avg_spend)
        pref.total_orders      = data.get('total_orders', pref.total_orders)
        pref.last_updated      = datetime.utcnow()
    else:
        pref = UserPreference(
            user_id=user_id,
            preferred_cuisine=data.get('preferred_cuisine'),
            avg_spend=data.get('avg_spend', 0),
            total_orders=data.get('total_orders', 0),
        )
        db.session.add(pref)
    db.session.commit()
    return jsonify(pref.to_dict())


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.route('/stats', methods=['GET'])
def get_stats():
    total_recs        = Recommendation.query.filter_by(is_active=True).count()
    total_restaurants = Restaurant.query.filter_by(is_active=True).count()
    premium_recs      = Recommendation.query.filter_by(is_active=True, tier='premium').count()
    wallet_unlocked   = Recommendation.query.filter_by(is_active=True, wallet_unlocked_premium=True).count()

    top = db.session.query(
        Recommendation.recommended_restaurant,
        db.func.count(Recommendation.id).label('cnt')
    ).filter_by(is_active=True) \
     .group_by(Recommendation.recommended_restaurant) \
     .order_by(db.desc('cnt')) \
     .first()

    return jsonify({
        'totalRecommendations': total_recs,
        'totalRestaurants': total_restaurants,
        'premiumRecommendations': premium_recs,
        'walletUnlockedPremiumCount': wallet_unlocked,
        'topRestaurant': top[0] if top else None,
    })


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_restaurants()

    app.run(port=8083, debug=True)
