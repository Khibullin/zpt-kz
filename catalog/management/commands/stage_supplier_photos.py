from django.core.management.base import BaseCommand
from catalog.photo_queue import stage_all_photos


class Command(BaseCommand):
    help='Stage missing supplier catalogue photos without changing stock or prices.'

    def handle(self,*args,**options):
        self.stdout.write(str(stage_all_photos()))
