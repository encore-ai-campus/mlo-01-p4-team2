from django.http import JsonResponse
from .service import read_state


class CampusFreezeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Authentication, logout and the pause controller remain accessible.
        if request.path.startswith(('/api/campus/freeze/', '/accounts/', '/api/accounts/', '/static/')):
            return self.get_response(request)
        guarded = request.path.startswith(('/api/', '/content/', '/learning/'))
        if guarded and request.user.is_authenticated:
            state = read_state()
            if state['paused']:
                response = JsonResponse({'error': state['message'], 'code': 'campus_frozen',
                                         'freeze': state}, status=423)
                response['Cache-Control'] = 'no-store'
                return response
        return self.get_response(request)
