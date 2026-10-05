from datetime import timedelta
from decimal import Decimal as D
from django.utils import timezone
from django.contrib.auth import get_user_model
from tenants.models import *; from erp.models import *
U_ = get_user_model()
_old = Tenant.objects.filter(slug='al-noor-traders').first()
if _old:
    Payment.objects.filter(tenant=_old).delete(); Invoice.objects.filter(tenant=_old).delete()
    Party.objects.filter(tenant=_old).delete(); Product.objects.filter(tenant=_old).delete(); _old.delete()
U_.objects.filter(username='pitch').delete()
t=Tenant.objects.create(name='Al-Noor Building Traders',slug='al-noor-traders',phone='0300-1112223',address='Model Town, Lahore')
t.features.set(Feature.objects.all()); u=U_.objects.create_user('pitch',password='Pitch@12345'); Membership.objects.create(user=u,tenant=t,is_owner=True)
U=lambda s:Unit.objects.get(symbol=s)
bag,kg=U('bag'),U('kg'); t.units.set([bag,kg,U('t'),U('pcs')]); t.default_unit=bag; t.save()
cement=Product.objects.create(tenant=t,name='Cement (50 kg bag)',unit=bag,price=1320)
steel=Product.objects.create(tenant=t,name='Sariya / Steel bar',unit=kg,price=285)
Product.objects.create(tenant=t,name='Bajri (Gravel)',unit=U('t'),price=4200)
cust=[Party.objects.create(tenant=t,name=n,kind='customer',phone=p,opening_balance=o) for n,p,o in [('Ahmed Ali Traders','0301-2345678',15000),('Bilal Builders','0321-7654321',0),('Chaudhry Construction','0333-1234567',42000),('Usman Hardware','0345-9876543',0)]]
ven=Party.objects.create(tenant=t,name='Maple Leaf Cement Dealer',kind='vendor',phone='0300-5550101')
today=timezone.localdate()
plan=[(24,0,cement,150,1310),(21,1,steel,2400,282),(18,2,cement,300,1315),(15,3,cement,80,1325),(12,0,steel,1800,284),(9,1,cement,220,1320),(6,2,steel,3000,283),(4,3,cement,120,1320),(2,0,cement,260,1318),(1,1,steel,1500,285)]
for ago,ci,prod,q,r in plan:
    d=today-timedelta(days=ago); i=Invoice.objects.create(tenant=t,kind='sale',party=cust[ci],date=d,due_date=d+timedelta(days=15))
    InvoiceItem.objects.create(invoice=i,product=prod,quantity=q,unit=prod.unit,rate=r); i.recalculate()
    if ci in (0,1,3): Payment.objects.create(tenant=t,party=cust[ci],invoice=i,direction='in',amount=(i.total*D('0.6')).quantize(D('1')),date=d,method='bank')
for ago,q in [(20,500),(8,600)]:
    d=today-timedelta(days=ago); i=Invoice.objects.create(tenant=t,kind='purchase',party=ven,date=d)
    InvoiceItem.objects.create(invoice=i,product=cement,quantity=q,unit=bag,rate=1250); i.recalculate()
    Payment.objects.create(tenant=t,party=ven,invoice=i,direction='out',amount=i.total/2,date=d)
print('seeded', Invoice.objects.filter(tenant=t).count())
