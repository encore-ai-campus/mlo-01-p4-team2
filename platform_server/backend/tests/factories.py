"""Legacy regression fixtures now create profile and position in separate tables."""
from accounts.models import Position, Profile
from eventing.catalog import class_group_uuid


def create_profile(**values):
    position = values.pop('last_position', None)
    observed_at = values.pop('last_position_at', None)
    if isinstance(values.get('cohort'), str):
        values['cohort_id'] = class_group_uuid(values.pop('cohort'))
    profile = Profile.objects.create(**values)
    Position.objects.create(profile=profile, last_position=position, last_position_at=observed_at)
    return profile
