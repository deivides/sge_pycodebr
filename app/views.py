from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from . import agent, metrics


@login_required(login_url='login')
def home(request):
    product_metrics = metrics.get_product_metrics()
    sales_metrics = metrics.get_sales_metrics()
    daily_sales_data = metrics.get_daily_sales_data()
    daily_sales_quantity_data = metrics.daily_sales_quantity_data()
    graphic_product_category_metrics = metrics.get_graphic_product_category_metrics()
    graphic_product_brand_metrics = metrics.get_graphic_product_brand_metrics()

    context = {
        'product_metrics': product_metrics,
        'sales_metrics': sales_metrics,
        'daily_sales_data': daily_sales_data,
        'daily_sales_quantity_data': daily_sales_quantity_data,
        'product_count_by_category': graphic_product_category_metrics,
        'product_count_by_brand': graphic_product_brand_metrics,
    }

    return render(request, 'home.html', context)


@login_required(login_url='login')
@permission_required('products.view_product', raise_exception=True)
@require_http_methods(['GET', 'POST'])
def agent_insights(request):
    # GET returns cached insights; POST asks for a fresh analysis.
    include_sales = request.user.has_perm('outflows.view_outflow')
    try:
        insights = agent.get_insights(include_sales, refresh=request.method == 'POST')
    except agent.AgentError as error:
        return JsonResponse({'error': str(error)}, status=503)
    return JsonResponse(insights)
