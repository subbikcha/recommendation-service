from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import requests

app = Flask(__name__)

# DATABASE CONFIG
app.config['SQLALCHEMY_DATABASE_URI'] = \
    'postgresql://ripple:ripple@localhost:5432/recommendation_db'

db = SQLAlchemy(app)

# ENTITY
class Recommendation(db.Model):

    __tablename__ = 'recommendations'

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.String(50))

    user_name = db.Column(db.String(100))

    tier = db.Column(db.String(50))

    recommended_restaurant = db.Column(db.String(100))

    boost_score = db.Column(db.Integer)

# CREATE RECOMMENDATION
@app.route('/recommendations/<user_id>', methods=['POST'])
def generate_recommendation(user_id):

    # CALL USER SERVICE
    response = requests.get(
        f'http://localhost:8081/users/{user_id}'
    )

    user = response.json()

    boost_score = 0

    # IMPORTANT BUSINESS LOGIC DEPENDENCY
    if user['tier'] == 'premium':
        boost_score = 10

    recommendation = Recommendation(
        user_id=user['userId'],
        user_name=user['userName'],
        tier=user['tier'],
        recommended_restaurant='Pizza Palace',
        boost_score=boost_score
    )

    db.session.add(recommendation)
    db.session.commit()

    return {
        'message': 'Recommendation generated',
        'boostScore': boost_score
    }

# GET ALL RECOMMENDATIONS
@app.route('/recommendations', methods=['GET'])
def get_recommendations():

    recommendations = Recommendation.query.all()

    result = []

    for r in recommendations:
        result.append({
            'id': r.id,
            'userId': r.user_id,
            'userName': r.user_name,
            'tier': r.tier,
            'restaurant': r.recommended_restaurant,
            'boostScore': r.boost_score
        })

    return result

# GET SINGLE RECOMMENDATION
@app.route('/recommendations/<int:id>', methods=['GET'])
def get_recommendation(id):

    r = Recommendation.query.get(id)

    if not r:
        return {'message': 'Not found'}, 404

    return {
        'id': r.id,
        'userId': r.user_id,
        'userName': r.user_name,
        'tier': r.tier,
        'restaurant': r.recommended_restaurant,
        'boostScore': r.boost_score
    }

if __name__ == '__main__':

    with app.app_context():
        db.create_all()

    app.run(port=8083, debug=True)