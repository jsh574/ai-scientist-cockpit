# 知识整合 Agent 代码实现说明

本文档根据当前代码说明知识整合 Agent 中 8 个子模块的实现方式，包括它们分别位于哪个 Python 文件、核心职责、是否调用大模型、如何进行代码校验，以及它们在总流程中的位置。

相关文件：

- `knowledge_integration_agent/agent.py`：知识整合 Agent 主流程与 8 个核心子模块。
- `knowledge_integration_agent/retrieval.py`：主流文献库与数据源 API 客户端。
- `knowledge_integration_agent/llm.py`：千问 Qwen 的 OpenAI-compatible 调用封装。
- `knowledge_integration_agent/adapter.py`：总控层调用知识整合 Agent 的输入裁剪适配器。

## 0. 整体运行流程

整体流程由 `KnowledgeIntegrationAgent.run()` 串起来，位于 `knowledge_integration_agent/agent.py`。

核心执行顺序如下：

```text
QuestionParser
  -> SearchQueryPlanner
  -> RetrievalService
  -> SourceVerifier
  -> SourceRelevanceFilter
  -> LiteratureExtractor
  -> EvidenceExtractor
  -> GapSynthesizer
  -> QualityReviewer
  -> 统一响应 metadata + payload + self_review
```

在代码中，`KnowledgeIntegrationAgent.__init__()` 初始化所有子模块：

```python
self.llm_client = RetryingLLMClient(llm_client or QwenDashScopeClient())
self.question_parser = QuestionParser()
self.search_query_planner = SearchQueryPlanner(self.llm_client)
self.retrieval_service = RetrievalService(literature_clients)
self.source_relevance_filter = SourceRelevanceFilter(self.llm_client)
self.source_verifier = SourceVerifier()
self.literature_extractor = LiteratureExtractor(self.llm_client)
self.evidence_extractor = EvidenceExtractor(self.llm_client)
self.gap_synthesizer = GapSynthesizer(self.llm_client)
self.quality_reviewer = QualityReviewer(self.llm_client)
```

可以看到：

- 问题卡片解析器是纯代码实现。
- 检索策略生成器调用大模型。
- 文献检索器调用外部 API。
- 来源真实性校验器是代码规则实现。
- 检索后相关性过滤器调用大模型删除低相关来源。
- 文献卡片抽取器调用大模型，并由代码补全/约束字段。
- 证据卡片抽取器调用大模型，并由代码绑定来源、过滤、修正字段。
- 知识空白识别器调用大模型，并由代码约束 gap 类型、证据 ID、字段格式。
- 模块自评器用代码算硬指标，用大模型评价内容质量。

`run()` 方法负责按顺序执行这些步骤，并最终返回统一响应：

```python
return {
    "metadata": self._metadata(request, status=status),
    "payload": {
        "literature_cards": literature_cards,
        "evidence_cards": evidence_cards,
        "knowledge_gaps": knowledge_gaps,
    },
    "self_review": self_review,
}
```

---

## 1. 问题卡片解析器 QuestionParser

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class QuestionParser
```

### 实现方式

问题卡片解析器是 **纯代码实现**，没有调用大模型。

它的输入是总控层传来的裁剪请求：

```python
request["input"]["question_card"]
```

代码首先定义必需字段：

```python
REQUIRED_QUESTION_FIELDS = [
    "core_question",
    "research_object",
    "key_concepts",
    "key_variables",
    "sub_questions",
    "search_keywords",
]
```

`parse()` 方法会检查这些字段是否存在且非空：

```python
missing = [
    f"question_card.{field}"
    for field in REQUIRED_QUESTION_FIELDS
    if not question_card.get(field)
]
```

如果缺字段，返回：

```python
return None, missing
```

如果字段完整，则组装成 `ParsedQuestion`：

```python
ParsedQuestion(
    task_id=request.get("task_id", ""),
    iteration=int(request.get("iteration", 1)),
    core_question=str(question_card["core_question"]),
    research_object=str(question_card["research_object"]),
    domain=list(question_card.get("domain") or []),
    key_concepts=list(question_card["key_concepts"]),
    key_variables=list(question_card["key_variables"]),
    sub_questions=list(question_card["sub_questions"]),
    search_keywords=list(question_card["search_keywords"]),
    search_policy=dict(request.get("input", {}).get("search_policy") or {}),
)
```

### 为什么不用大模型

这个模块只做字段读取、字段校验和类型转换。它不需要语义推理，因此用代码实现更稳定，也更容易让总控层判断失败原因。

---

## 2. 检索策略生成器 SearchQueryPlanner

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class SearchQueryPlanner
```

### 实现方式

检索策略生成器是 **大模型 + 代码约束** 的混合实现。

初始化时注入 `llm_client`：

```python
def __init__(self, llm_client: LLMClient) -> None:
    self.llm_client = llm_client
```

它不会使用固定模板机械拼接检索式，而是把问题卡片中的完整语义信息发给大模型：

```python
payload = {
    "core_question": question.core_question,
    "research_object": question.research_object,
    "domain": question.domain,
    "key_concepts": question.key_concepts,
    "key_variables": question.key_variables,
    "sub_questions": question.sub_questions,
    "search_keywords": question.search_keywords,
    "instruction": (
        "Generate topic-specific scholarly search queries. Avoid fixed templates. "
        "Use professional English terms, important synonyms, observable proxies, "
        "method terms, and domain databases when useful."
    ),
}
```

调用大模型：

```python
result = self.llm_client.generate_json(
    system_prompt=(
        "You design retrieval strategies for Science 125 frontier questions. "
        "Return {'queries': [{'query': string, 'rationale': string, 'priority': number}]}."
    ),
    user_payload=payload,
    expected_schema="search_strategy",
)
```

这里的 `system_prompt` 明确告诉模型：它面对的是 Science 125 前沿科学问题，需要生成主题自适应的学术检索策略。

### 代码做了哪些约束

大模型返回后，代码负责排序、去重、过滤空 query、限制数量。

按优先级排序：

```python
for item in sorted(
    result.get("queries") or [],
    key=lambda query: float(query.get("priority", 0.5)),
    reverse=True,
):
```

去除重复 query：

```python
query_text = " ".join(str(item.get("query", "")).split())
key = query_text.lower()
if not query_text or key in seen:
    continue
seen.add(key)
```

限制最大检索式数量：

```python
max_queries = int(question.search_policy.get("max_queries") or 8)
...
if len(queries) >= max_queries:
    break
```

修正 priority 到 0 到 1：

```python
"priority": _clamp_score(item.get("priority", 0.5))
```

### 这一模块的效果

它不是固定生成：

```text
research_object + concept
```

而是让大模型根据学科主题生成更合适的检索式。例如天文学问题会倾向生成观测指标、仪器、数据源相关 query；生命科学问题会生成机制、通路、实验模型相关 query；材料科学问题会生成材料体系、表征方法、催化性能指标相关 query。

---

## 3. 文献与数据源检索器 RetrievalService

文件位置：

```text
knowledge_integration_agent/agent.py
knowledge_integration_agent/retrieval.py
```

主流程类：

```python
class RetrievalService
```

API 客户端类：

```python
class SemanticScholarClient
class OpenAlexClient
class CrossrefClient
class ArxivClient
class PubMedClient
class EuropePmcClient
class NasaAdsClient
```

### 实现方式

文献与数据源检索器是 **代码/API 实现**，不靠大模型凭空生成文献。

在 `RetrievalService.__init__()` 中，如果外部没有注入测试客户端，就使用默认文献库客户端：

```python
self.clients = clients or default_literature_clients()
```

默认客户端在 `retrieval.py` 中定义：

```python
def default_literature_clients() -> list[LiteratureClient]:
    return [
        SemanticScholarClient(),
        OpenAlexClient(),
        CrossrefClient(),
        ArxivClient(),
        PubMedClient(),
        EuropePmcClient(),
        NasaAdsClient(),
    ]
```

### 检索流程

`RetrievalService.retrieve()` 接收大模型生成的 queries，然后依次调用每个文献库客户端：

```python
for query in queries:
    for client in self.clients:
        try:
            sources.extend(client.search(query["query"], limit=per_client_limit))
        except Exception:
            continue
```

这里每个客户端都必须实现统一接口：

```python
def search(self, query: str, *, limit: int) -> list[dict[str, Any]]
```

每个检索结果都会被标准化为统一 source record：

```python
{
    "title": title,
    "authors": authors,
    "year": year,
    "source": source,
    "doi": doi,
    "url": url,
    "literature_type": literature_type,
    "abstract": abstract,
    "database": database,
    "raw": raw,
}
```

这个结构由 `_source_record()` 生成。

### 已接入的数据源

#### Semantic Scholar

文件：`retrieval.py`

接口：

```python
https://api.semanticscholar.org/graph/v1/paper/search
```

请求字段包括：

```python
title, authors, year, venue, externalIds, url, abstract, publicationTypes
```

#### OpenAlex

接口：

```python
https://api.openalex.org/works
```

代码还实现了 `_openalex_abstract()`，用于把 OpenAlex 的 inverted index 摘要还原成普通文本。

#### Crossref

接口：

```python
https://api.crossref.org/works
```

用于获取 DOI、标题、作者、发表时间、期刊来源等。

#### arXiv

接口：

```python
https://export.arxiv.org/api/query
```

返回 XML，因此代码使用：

```python
xml.etree.ElementTree
```

解析 Atom feed。

#### PubMed

接口：

```python
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi
```

先搜索 PMID，再用 summary 接口取标题、作者、期刊等元数据。

#### Europe PMC

接口：

```python
https://www.ebi.ac.uk/europepmc/webservices/rest/search
```

用于补充生物医学方向的文献与摘要。

#### NASA ADS

接口：

```python
https://api.adsabs.harvard.edu/v1/search/query
```

适合天文、宇宙学、物理方向问题。它需要：

```text
NASA_ADS_TOKEN
```

如果没有 token，代码直接返回空列表：

```python
if not self.token:
    return []
```

这可以避免未配置 token 时整个系统报错。

### 去重与数量限制

检索完成后会调用 `_dedupe_sources()`：

```python
return _dedupe_sources(sources)[:max_papers]
```

去重逻辑优先使用 DOI，没有 DOI 时使用标题：

```python
doi = str(source.get("doi") or "").lower().strip()
title = " ".join(str(source.get("title") or "").lower().split())
key = doi or title
```

---

## 4. 来源真实性校验器 SourceVerifier

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class SourceVerifier
```

### 实现方式

来源真实性校验器是 **代码规则实现**。

它不让大模型判断文献真假，因为大模型可能会误判或幻觉。

入口方法：

```python
def verify(self, sources: list[dict[str, Any]], must_verify: bool = True) -> list[dict[str, Any]]:
```

如果 `must_verify` 为 `False`，直接返回原始 sources：

```python
if not must_verify:
    return sources
```

如果必须验证，则过滤掉不可验证来源：

```python
return [source for source in sources if self._is_verifiable(source)]
```

当前可验证条件是：

```python
doi = str(source.get("doi") or "").strip()
url = str(source.get("url") or "").strip()
title = str(source.get("title") or "").strip()
has_external_id = doi.startswith("10.") or url.startswith(("https://", "http://"))
return bool(title and has_external_id)
```

也就是说，一条来源至少需要：

- 有标题
- 有 DOI，且 DOI 以 `10.` 开头；或有 http/https URL

### 它解决什么问题

它阻止以下来源进入正式知识整合结果：

- 没有标题的结果
- 没有 DOI 的伪文献
- 没有 URL 的不可追踪记录
- 大模型或外部检索中出现的低质量来源

---

## 4.5 检索后相关性过滤器 SourceRelevanceFilter

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class SourceRelevanceFilter
```

### 实现方式

检索后相关性过滤器是 **大模型 + 代码阈值过滤**。

它位于 `SourceVerifier` 之后、`LiteratureExtractor` 之前：

```python
verified_sources = self.source_verifier.verify(...)
relevant_sources = self.source_relevance_filter.filter(question, verified_sources)
literature_cards = self.literature_extractor.extract(relevant_sources)
```

也就是说，外部 API 检索回来的文献会先经过 DOI/URL 等真实性校验，再交给大模型判断是否真正与当前问题相关。相关性低的来源不会进入 `literature_cards`、`evidence_cards` 和 `knowledge_gaps`。

大模型需要返回：

```python
{
    "decisions": [
        {
            "source_index": 0,
            "relevance_score": 0.9,
            "keep": true,
            "reason": "topic match"
        }
    ]
}
```

代码负责检查：

- `source_index` 是否是有效数字。
- `keep` 是否为 `true`。
- `relevance_score` 是否达到阈值。

阈值来自：

```python
question.search_policy.get("relevance_threshold", 0.5)
```

如果没有配置，默认相关性阈值是 `0.5`。

---

## 5. 文献卡片抽取器 LiteratureExtractor

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class LiteratureExtractor
```

### 实现方式

文献卡片抽取器是 **大模型 + 代码校验**。

大模型负责抽取语义字段：

- `main_findings`
- `related_concepts`

代码负责生成和固定数据规范字段：

- `literature_id`
- `title`
- `authors`
- `year`
- `source`
- `doi`
- `url`
- `literature_type`
- `relevance_score`
- `main_findings`
- `related_concepts`

### 大模型抽取部分

`_extract_llm_fields()` 会调用：

```python
self.llm_client.generate_json(
    system_prompt=(
        "Extract literature card semantic fields from a verified scholarly source. "
        "Return {'literature_cards': [{'main_findings': string[], "
        "'related_concepts': string[]}]} only."
    ),
    user_payload={
        "title": source.get("title"),
        "abstract": source.get("abstract"),
        "source": source.get("source"),
        "database": source.get("database"),
    },
    expected_schema="literature_cards",
)
```

大模型只负责从标题、摘要、来源数据库中识别主要发现和相关概念。

### 代码校验和字段补全部分

`extract()` 方法会逐篇 source 生成卡片：

```python
{
    "literature_id": f"lit_{index:03d}",
    "title": str(source.get("title", "")),
    "authors": list(source.get("authors") or []),
    "year": int(source.get("year") or 0),
    "source": str(source.get("source", "")),
    "doi": str(source.get("doi", "")),
    "url": str(source.get("url", "")),
    "literature_type": str(source.get("literature_type", "other")),
    "relevance_score": _clamp_score(source.get("relevance_score", 0.75)),
    "main_findings": _string_list(llm_card.get("main_findings")),
    "related_concepts": _string_list(llm_card.get("related_concepts")),
}
```

其中：

- `literature_id` 由代码生成，确保稳定格式 `lit_001`。
- `relevance_score` 用 `_clamp_score()` 限制在 0 到 1。
- `main_findings` 和 `related_concepts` 用 `_string_list()` 确保是字符串数组。

---

## 6. 证据卡片抽取器 EvidenceExtractor

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class EvidenceExtractor
```

### 实现方式

证据卡片抽取器是 **大模型 + 代码校验**。

大模型负责：

- 提取 `claim`
- 判断 `evidence_type`
- 判断 `support_direction`
- 识别 `related_concepts`
- 给出 `strength_score`
- 总结 `summary`
- 总结 `limitations`

代码负责：

- 生成 `evidence_id`
- 强制绑定 `source_literature_id`
- 检查 `source_literature_id` 是否存在
- 限制 `strength_score` 在 0 到 1
- 过滤空 claim
- 保证输出字段符合数据规范

### 大模型调用部分

每篇文献都会调用一次大模型：

```python
result = self.llm_client.generate_json(
    system_prompt=(
        "Extract evidence cards from source text. Return "
        "{'evidence_cards': [{'claim': string, 'evidence_type': string, "
        "'support_direction': 'support|oppose|uncertain', "
        "'related_concepts': string[], 'strength_score': number, "
        "'summary': string, 'limitations': string[]}]} only."
    ),
    user_payload={
        "source_literature_id": literature["literature_id"],
        "title": literature["title"],
        "abstract": source.get("abstract", ""),
        "main_findings": literature.get("main_findings", []),
        "related_concepts": literature.get("related_concepts", []),
    },
    expected_schema="evidence_cards",
)
```

### 代码校验部分

如果大模型返回的 item 不是字典，跳过：

```python
if not isinstance(item, dict):
    continue
```

如果 claim 为空，跳过：

```python
claim = str(item.get("claim", "")).strip()
if not claim:
    continue
```

证据 ID 由代码生成：

```python
"evidence_id": f"ev_{evidence_index:03d}"
```

来源文献 ID 不相信大模型返回，而是由当前 literature 强制绑定：

```python
"source_literature_id": literature["literature_id"]
```

支持方向通过 `_support_direction()` 限制为：

```text
support
oppose
uncertain
```

强度分数通过 `_clamp_score()` 限制到 0 到 1：

```python
"strength_score": _clamp_score(item.get("strength_score", 0.5))
```

最后再次检查证据来源是否存在于文献卡片 ID 集合中：

```python
literature_ids = {card["literature_id"] for card in literature_cards}
return [
    card
    for card in evidence_cards
    if card["source_literature_id"] in literature_ids
]
```

这保证了没有来源的证据不会进入输出。

---

## 7. 知识空白识别器 GapSynthesizer

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class GapSynthesizer
```

### 实现方式

知识空白识别器是 **大模型为主 + 规则约束**。

大模型负责根据问题和证据识别：

- 机制不明
- 因果不确定
- 数据缺口
- 方法限制
- 结论矛盾
- 哪些 gap 可以驱动假设生成

代码负责：

- 生成 `gap_id`
- 限制 `gap_type`
- 检查 `related_evidence_ids` 是否真实存在
- 缺少证据 ID 时自动绑定已有证据
- 缺少相关概念时用问题卡片中的关键概念补齐
- 过滤缺少 `description` 或 `why_it_matters_for_hypothesis_generation` 的 gap
- 限制 `importance_score` 在 0 到 1

### 大模型调用部分

```python
result = self.llm_client.generate_json(
    system_prompt=(
        "Identify knowledge gaps that can drive hypothesis generation. Return "
        "{'knowledge_gaps': [{'description': string, 'gap_type': string, "
        "'related_concepts': string[], 'related_evidence_ids': string[], "
        "'importance_score': number, "
        "'why_it_matters_for_hypothesis_generation': string}]} only."
    ),
    user_payload={
        "question": {
            "core_question": question.core_question,
            "research_object": question.research_object,
            "domain": question.domain,
            "key_concepts": question.key_concepts,
            "sub_questions": question.sub_questions,
        },
        "evidence_cards": evidence_cards,
    },
    expected_schema="knowledge_gaps",
)
```

### 代码约束部分

先收集有效证据 ID：

```python
valid_evidence_ids = {card["evidence_id"] for card in evidence_cards}
```

只保留真实存在的证据 ID：

```python
related_ids = [
    evidence_id
    for evidence_id in _string_list(item.get("related_evidence_ids"))
    if evidence_id in valid_evidence_ids
]
```

如果大模型没有绑定有效证据，但当前确实有 evidence_cards，则默认绑定第一条证据：

```python
if not related_ids and evidence_cards:
    related_ids = [evidence_cards[0]["evidence_id"]]
```

如果大模型没有给出相关概念，则使用问题卡片的前两个关键概念补齐：

```python
if not related_concepts:
    related_concepts = question.key_concepts[:2]
```

如果没有描述或没有说明为什么能服务假设生成，则跳过：

```python
if not description or not why:
    continue
```

gap 类型通过 `_gap_type()` 限制到允许集合：

```python
ALLOWED_GAP_TYPES = {
    "mechanism_unknown",
    "causal_uncertain",
    "data_missing",
    "method_limitation",
    "contradiction",
}
```

最终生成：

```python
{
    "gap_id": f"gap_{index:03d}",
    "description": description,
    "gap_type": _gap_type(item.get("gap_type")),
    "related_concepts": related_concepts,
    "related_evidence_ids": related_ids,
    "importance_score": _clamp_score(item.get("importance_score", 0.5)),
    "why_it_matters_for_hypothesis_generation": why,
}
```

---

## 8. 模块自评器 QualityReviewer

文件位置：

```text
knowledge_integration_agent/agent.py
```

对应类：

```python
class QualityReviewer
```

### 实现方式

模块自评器是 **代码硬指标 + 大模型内容评价**。

代码负责评价固定、可计算的部分：

- 来源是否可验证
- 证据是否可追踪
- 知识空白数量是否足够
- 是否缺少文献、证据、知识空白

大模型负责评价内容质量：

- 文献总结是否合理
- 证据是否真正支持问题
- 知识空白是否有科研价值
- 是否存在明显遗漏
- 后续改进建议

### 代码硬指标

先取所有文献 ID：

```python
literature_ids = {card["literature_id"] for card in literature_cards}
```

检查证据是否能追溯到文献：

```python
traceable_evidence = [
    card
    for card in evidence_cards
    if card.get("source_literature_id") in literature_ids
]
```

计算证据可追踪率：

```python
traceability = len(traceable_evidence) / len(evidence_cards) if evidence_cards else 0.0
```

计算来源可验证分数：

```python
source_verifiability = 1.0 if literature_cards else 0.0
```

计算知识空白价值基础分：

```python
gap_value = min(1.0, len(knowledge_gaps) / 3)
```

### 大模型内容评价

调用大模型：

```python
llm_result = self.llm_client.generate_json(
    system_prompt=(
        "Evaluate content quality for a knowledge integration stage. Return "
        "{'content_quality_score': number, 'issues': string[], 'suggestions': string[]}."
    ),
    user_payload={
        "question": question.core_question,
        "literature_cards": literature_cards,
        "evidence_cards": evidence_cards,
        "knowledge_gaps": knowledge_gaps,
    },
    expected_schema="self_review",
)
```

代码把大模型返回的内容质量分数限制在 0 到 1：

```python
content_quality = _clamp_score(llm_result.get("content_quality_score", 0.5))
```

最终总分是五项平均：
最终总分是四项平均：

```python
overall = round(
    (
        source_verifiability
        + traceability
        + gap_value
        + content_quality
    )
    / 4,
    2,
)
```

阈值固定为：

```python
threshold = 0.75
```

最终返回：

```python
{
    "passed": overall >= threshold,
    "overall_score": overall,
    "threshold": threshold,
    "dimension_scores": {
        "source_verifiability": round(source_verifiability, 2),
        "evidence_traceability": round(traceability, 2),
        "gap_value": round(gap_value, 2),
        "content_quality": content_quality,
    },
    "issues": issues,
    "suggestions": suggestions,
}
```

---

## 9. 大模型调用封装 QwenDashScopeClient

文件位置：

```text
knowledge_integration_agent/llm.py
```

对应类：

```python
class QwenDashScopeClient
```

当前 Agent 实际使用时会把 `QwenDashScopeClient` 包在 `RetryingLLMClient` 里。每次大模型 JSON 调用最多自主重试 3 次；如果 3 次都失败，异常会继续抛给 `KnowledgeIntegrationAgent.run()`，最终返回 `metadata.status = "failed"` 和失败原因。

### 配置方式

`llm.py` 顶部提供本地一次性配置：

```python
QWEN_API_KEY = "..."
QWEN_MODEL = "qwen3.6-flash"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

代码读取配置的优先级是：

```text
构造函数传入 api_key/model
-> 环境变量 DASHSCOPE_API_KEY / QWEN_API_KEY / QWEN_MODEL
-> llm.py 顶部常量
```

实现代码：

```python
self.api_key = (
    api_key
    or os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("QWEN_API_KEY")
    or QWEN_API_KEY
)
self.model = model or os.getenv("QWEN_MODEL") or QWEN_MODEL
```

### 调用方式

大模型请求使用 OpenAI-compatible chat completions：

```python
request = urllib.request.Request(
    f"{self.base_url}/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {self.api_key}",
        "Content-Type": "application/json",
    },
    method="POST",
)
```

为了让模型返回 JSON，payload 中设置：

```python
"response_format": {"type": "json_object"}
```

同时 system prompt 会追加：

```python
"Return strict JSON only ..."
```

返回内容会通过 `_parse_json_content()` 解析。如果模型意外返回了 markdown 或额外文本，代码会尝试用正则提取第一个 JSON 对象。

---

## 10. 总控层适配器 KnowledgeIntegrationAdapter

文件位置：

```text
knowledge_integration_agent/adapter.py
```

对应类：

```python
class KnowledgeIntegrationAdapter
```

### 作用

这个类负责总控层和知识整合 Agent 之间的接口裁剪。

它保证模块 2 不会拿到完整 `task_context`，只拿到：

```python
"question_card"
"search_policy"
```

代码：

```python
def build_request(self, task_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task_context.get("task_id", ""),
        "stage": self.stage,
        "iteration": int(task_context.get("iteration", 1)),
        "input": {
            "question_card": task_context.get("question_card"),
            "search_policy": dict(self.default_search_policy),
        },
        "output_schema": self.output_schema,
    }
```

默认检索策略：

```python
{
    "max_papers": 20,
    "min_recent_papers": 5,
    "must_verify_sources": True,
    "forbidden_actions": ["invent_references", "invent_dataset_url"],
}
```

这符合数据规范中“每个模块只读取自己需要的数据对象”的要求。

---

## 11. 各子模块实现方式总表

| 子模块 | Python 文件 | 实现方式 | 大模型职责 | 代码职责 |
|---|---|---|---|---|
| 问题卡片解析器 | `agent.py` | 纯代码 | 无 | 校验必填字段，转换为 `ParsedQuestion` |
| 检索策略生成器 | `agent.py` | 大模型 + 代码 | 生成主题自适应检索式 | 去重、排序、过滤、限制数量 |
| 文献与数据源检索器 | `agent.py`, `retrieval.py` | 代码/API | 无 | 调用 Semantic Scholar、OpenAlex、Crossref、arXiv、PubMed、Europe PMC、NASA ADS |
| 来源真实性校验器 | `agent.py` | 纯代码 | 无 | 检查标题、DOI、URL |
| 检索后相关性过滤器 | `agent.py` | 大模型 + 代码阈值 | 判断检索结果是否和当前问题相关 | 按 `relevance_threshold` 删除低相关来源 |
| 文献卡片抽取器 | `agent.py` | 大模型 + 代码 | 抽取 `main_findings`、`related_concepts` | 生成 ID，补齐字段，限制分数，规范数组 |
| 证据卡片抽取器 | `agent.py` | 大模型 + 代码 | 提取 claim、证据类型、支持方向、限制等 | 绑定来源 ID，过滤空 claim，限制分数，校验证据可追踪 |
| 知识空白识别器 | `agent.py` | 大模型 + 规则约束 | 生成 knowledge gaps | 限制 gap 类型，校验证据 ID，补齐概念，过滤无效 gap |
| 模块自评器 | `agent.py` | 大模型 + 代码 | 评价内容质量、给出问题和建议 | 计算来源可验证性、证据追踪率、gap 数量、内容质量四项均值 |

---

## 12. 总结

当前知识整合 Agent 已经从早期的示例型实现改成了通用型实现，适用于《Science》125 个前沿科学问题，而不是只面向阿尔茨海默病或病理领域。

它的核心设计原则是：

```text
凡是事实检索、字段校验、ID 生成、来源追踪、分数范围控制，用代码/API。
凡是语义理解、检索策略扩展、文献发现抽取、证据 claim 抽取、知识空白判断、内容质量评价，用大模型。
```

这样既能利用大模型的科学语义理解能力，又能通过代码控制格式、来源真实性和总控层可验证性。
