from django.shortcuts import render

from django.shortcuts import render


def dashboard(request):
    return render(request, 'newui/dashboard.html', {})


def transfers(request):
    return render(request, 'newui/transfers.html', {})


def transfer_progress(request, uuid):
    return render(request, 'newui/transfer_progress.html', {
        'uuid': uuid,
    })
