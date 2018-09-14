from django.shortcuts import render

from django.shortcuts import render


def dashboard(request):
    return render(request, 'newui/dashboard.html', {})

def transfers(request):
    return render(request, 'newui/transfers.html', {})
