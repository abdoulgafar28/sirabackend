from rest_framework import serializers
from apps.reviews.models import Review


class ReviewCreateSerializer(serializers.ModelSerializer):
    """Client note un conducteur après une course."""
    class Meta:
        model  = Review
        fields = ['ride', 'rating', 'comment']

    def validate_ride(self, value):
        from apps.rides.models import Ride
        user = self.context['request'].user

        # Vérifier que la course appartient au client
        if value.client != user:
            raise serializers.ValidationError("Vous ne pouvez noter que vos propres courses.")

        # Vérifier que la course est terminée
        if value.status != Ride.Status.COMPLETED:
            raise serializers.ValidationError("Vous ne pouvez noter qu'une course terminée.")

        # Vérifier qu'aucun avis n'existe déjà
        if hasattr(value, 'review'):
            raise serializers.ValidationError("Vous avez déjà évalué cette course.")

        return value

    def create(self, validated_data):
        ride   = validated_data['ride']
        review = Review.objects.create(
            client=self.context['request'].user,
            driver=ride.driver,
            **validated_data
        )
        # Recalcul de la note moyenne du conducteur
        self._update_driver_rating(ride.driver)
        return review

    def _update_driver_rating(self, driver_profile):
        from django.db.models import Avg
        result = Review.objects.filter(driver=driver_profile).aggregate(avg=Avg('rating'))
        driver_profile.average_rating = result['avg'] or 0
        driver_profile.total_reviews  = Review.objects.filter(driver=driver_profile).count()
        driver_profile.save(update_fields=['average_rating', 'total_reviews'])


class ReviewListSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    client_photo = serializers.ImageField(source='client.photo', read_only=True)

    class Meta:
        model  = Review
        fields = [
            'id', 'client_name', 'client_photo',
            'rating', 'comment', 'created_at',
        ]


class ReviewDetailSerializer(serializers.ModelSerializer):
    client_name  = serializers.CharField(source='client.full_name', read_only=True)
    driver_name  = serializers.CharField(source='driver.user.full_name', read_only=True)

    class Meta:
        model  = Review
        fields = '__all__'