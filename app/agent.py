"""SGE agent: asks Claude for stock and sales insights based on the database records."""
import json
import logging
from datetime import timedelta
from decimal import Decimal

import anthropic
from django.core.cache import cache
from django.db.models import Count, F, Sum
from django.utils import timezone

from inflows.models import Inflow
from outflows.models import Outflow
from products.models import Product

logger = logging.getLogger(__name__)

MODEL = 'claude-opus-5-5'
CACHE_SECONDS = 30 * 60
REFRESH_COOLDOWN_SECONDS = 60
PRODUCT_SAMPLE_SIZE = 40

SYSTEM_PROMPT = """Você é o Agente SGE, o analista de estoque integrado ao SGE (Sistema de Gestão de Estoque).
Você recebe um resumo em JSON dos registros do sistema: produtos, entradas e, quando o usuário tem acesso, saídas (vendas).

Gere insights práticos para quem gerencia o estoque: riscos (ruptura, produtos parados, margens baixas ou negativas),
oportunidades (produtos que vendem bem, reposição, preços) e tendências (vendas e entradas recentes).

Regras:
- Responda em português do Brasil, com frases curtas e diretas.
- Use somente os dados recebidos. Cite produtos, categorias, marcas e números concretos; nunca invente valores.
- Valores monetários em reais no formato R$ 1.234,56.
- Se houver poucos dados para uma conclusão, diga isso em vez de especular.
- Entregue de 3 a 5 insights, do mais importante para o menos importante, e um resumo geral de uma ou duas frases."""

OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'insights': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'kind': {'type': 'string', 'enum': ['alerta', 'oportunidade', 'tendencia', 'info']},
                    'title': {'type': 'string'},
                    'detail': {'type': 'string'},
                },
                'required': ['kind', 'title', 'detail'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['summary', 'insights'],
    'additionalProperties': False,
}


class AgentError(Exception):
    """Raised with a user-facing message when insights cannot be generated."""


def _money(value):
    return float((value or Decimal('0')).quantize(Decimal('0.01')))


def _product_row(product):
    margin = None
    if product.selling_price:
        margin = round(float((product.selling_price - product.cost_price) / product.selling_price * 100), 1)
    return {
        'produto': product.title,
        'categoria': product.category.name,
        'marca': product.brand.name,
        'estoque': product.quantity,
        'preco_custo': _money(product.cost_price),
        'preco_venda': _money(product.selling_price),
        'margem_pct': margin,
    }


def build_snapshot(include_sales):
    """Summarizes the database into a bounded JSON-friendly dict for the model."""
    today = timezone.localdate()
    since_30 = timezone.now() - timedelta(days=30)
    products = Product.objects.select_related('category', 'brand')

    stock_value = products.aggregate(
        custo=Sum(F('cost_price') * F('quantity')),
        venda=Sum(F('selling_price') * F('quantity')),
        unidades=Sum('quantity'),
    )
    snapshot = {
        'data_referencia': today.isoformat(),
        'estoque': {
            'produtos_cadastrados': products.count(),
            'unidades_em_estoque': stock_value['unidades'] or 0,
            'custo_total': _money(stock_value['custo']),
            'valor_venda_total': _money(stock_value['venda']),
            'produtos_sem_estoque': products.filter(quantity__lte=0).count(),
        },
        'menores_estoques': [_product_row(p) for p in products.order_by('quantity', 'title')[:PRODUCT_SAMPLE_SIZE]],
        'maiores_valores_em_estoque': [
            _product_row(p) for p in products.annotate(valor=F('selling_price') * F('quantity')).order_by('-valor')[:PRODUCT_SAMPLE_SIZE]
        ],
        'por_categoria': list(
            products.values(nome=F('category__name')).annotate(produtos=Count('id'), unidades=Sum('quantity')).order_by('-unidades')
        ),
        'por_marca': list(
            products.values(nome=F('brand__name')).annotate(produtos=Count('id'), unidades=Sum('quantity')).order_by('-unidades')
        ),
        'entradas_ultimos_30_dias': list(
            Inflow.objects.filter(created_at__gte=since_30)
            .values(produto=F('product__title'), fornecedor=F('supplier__name'))
            .annotate(registros=Count('id'), unidades=Sum('quantity'))
            .order_by('-unidades')[:PRODUCT_SAMPLE_SIZE]
        ),
    }

    if include_sales:
        recent = Outflow.objects.filter(created_at__gte=since_30)
        by_product = (
            recent.values(produto=F('product__title'))
            .annotate(vendas=Count('id'), unidades=Sum('quantity'), receita=Sum(F('quantity') * F('product__selling_price')))
            .order_by('-receita')
        )
        sold_ids = recent.values('product_id')
        daily = (
            Outflow.objects.filter(created_at__date__gte=today - timedelta(days=13))
            .values(dia=F('created_at__date'))
            .annotate(vendas=Count('id'), unidades=Sum('quantity'), receita=Sum(F('quantity') * F('product__selling_price')))
            .order_by('dia')
        )
        idle = products.filter(quantity__gt=0).exclude(id__in=sold_ids)
        snapshot['vendas_ultimos_30_dias'] = {
            'total_vendas': recent.count(),
            'por_produto': [
                {**row, 'receita': _money(row['receita'])} for row in by_product[:PRODUCT_SAMPLE_SIZE]
            ],
            'por_dia_ultimos_14_dias': [
                {**row, 'dia': row['dia'].isoformat(), 'receita': _money(row['receita'])} for row in daily
            ],
            'produtos_com_estoque_sem_vendas': {
                'quantidade': idle.count(),
                'exemplos': [_product_row(p) for p in idle.order_by('-quantity')[:20]],
            },
        }

    return snapshot


def _ask_claude(snapshot):
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=SYSTEM_PROMPT,
            output_config={
                'effort': 'medium',
                'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA},
            },
            messages=[{
                'role': 'user',
                'content': 'Registros atuais do SGE:\n' + json.dumps(snapshot, ensure_ascii=False, default=str),
            }],
        )
    except anthropic.AuthenticationError:
        raise AgentError('O Agente SGE não está configurado: a chave da API da Anthropic é inválida.')
    except anthropic.RateLimitError:
        raise AgentError('O Agente SGE está com muitas solicitações. Tente novamente em alguns minutos.')
    except anthropic.APIStatusError as error:
        logger.error('SGE agent API error %s: %s', error.status_code, error.message)
        raise AgentError('O Agente SGE não conseguiu analisar os dados agora. Tente novamente mais tarde.')
    except anthropic.APIConnectionError:
        raise AgentError('Não foi possível conectar ao serviço de IA. Verifique a conexão do servidor.')
    except (anthropic.AnthropicError, TypeError) as error:
        # The SDK raises TypeError before sending the request when no credentials are configured.
        logger.error('SGE agent client error: %s', error)
        raise AgentError('O Agente SGE não está configurado. Defina a variável de ambiente ANTHROPIC_API_KEY no servidor.')

    if response.stop_reason == 'refusal':
        raise AgentError('O Agente SGE não pôde gerar insights para estes dados.')
    if response.stop_reason == 'max_tokens':
        raise AgentError('A análise ficou longa demais. Tente atualizar novamente.')

    text = next((block.text for block in response.content if block.type == 'text'), '')
    return json.loads(text)


def get_insights(include_sales, refresh=False):
    """Returns cached insights, generating new ones when missing, expired or explicitly refreshed."""
    scope = 'sales' if include_sales else 'stock'
    cache_key = f'sge_agent:insights:{scope}'
    cooldown_key = f'sge_agent:cooldown:{scope}'

    cached = cache.get(cache_key)
    if cached and (not refresh or cache.get(cooldown_key)):
        return {**cached, 'cached': True}

    result = _ask_claude(build_snapshot(include_sales))
    result['generated_at'] = timezone.now().isoformat()
    cache.set(cache_key, result, CACHE_SECONDS)
    cache.set(cooldown_key, True, REFRESH_COOLDOWN_SECONDS)
    return {**result, 'cached': False}
