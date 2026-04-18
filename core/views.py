from django.shortcuts import render


def home(request):
    return render(request, "core/home.html")


def testpage(request):
    return render(request, "core/testpage.html")