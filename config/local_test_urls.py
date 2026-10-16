"""Local browser assets are served without third-party network access."""
from pathlib import Path
import plotly
from django.http import FileResponse, JsonResponse
from django.urls import include, path

provider_tracker = []


def provider_status(request):
    if not provider_tracker:
        return JsonResponse({"error": "Local test WSGI instrumentation is missing"}, status=503)
    return JsonResponse(provider_tracker[0].snapshot())


def plotly_script(request):
    return FileResponse((Path(plotly.__file__).parent / "package_data/plotly.min.js").open("rb"), content_type="application/javascript")


urlpatterns = [
    path("__local_test__/providers", provider_status),
    path("__local_test__/plotly.js", plotly_script),
    path("", include("config.urls")),
]
