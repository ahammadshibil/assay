"""PubMed parsing + merge tests — offline, no network."""
from assay.models import Work
from assay.pubmed import parse_pubmed_xml
from assay.science import _merge_works, _work_key

_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <Journal><Title>Nature Communications</Title>
          <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
        <ArticleTitle>PAM-flexible engineered FnCas9 variants</ArticleTitle>
        <Abstract><AbstractText>FnCas9 is highly precise with negligible mismatch affinity.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Acharya</LastName><ForeName>Sundaram</ForeName></Author>
          <Author><LastName>Chakraborty</LastName><ForeName>Debojyoti</ForeName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1038/s41467-024-0001</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>222</PMID>
      <Article>
        <Journal><Title>Trends in Genetics</Title>
          <JournalIssue><PubDate><MedlineDate>2023 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
        <ArticleTitle>A review of CRISPR diagnostics</ArticleTitle>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
          <PublicationType>Review</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>333</PMID>
      <Article>
        <Journal><Title>Some Journal</Title>
          <JournalIssue><PubDate><Year>2019</Year></PubDate></JournalIssue></Journal>
        <ArticleTitle>A finding later withdrawn</ArticleTitle>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
          <PublicationType>Retracted Publication</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_parse_primary_article():
    works = parse_pubmed_xml(_XML)
    w = works[0]
    assert w.title.startswith("PAM-flexible")
    assert w.year == 2024
    assert w.venue == "Nature Communications"
    assert w.is_primary_article is True
    assert w.is_preprint is False
    assert w.doi == "10.1038/s41467-024-0001"
    assert "Sundaram Acharya" in w.authors
    assert "negligible mismatch" in (w.abstract or "")


def test_parse_review_not_primary():
    review = parse_pubmed_xml(_XML)[1]
    assert review.is_primary_article is False     # authoritative: PublicationType=Review
    assert review.year == 2023                    # parsed from MedlineDate


def test_parse_retracted_flag():
    retracted = parse_pubmed_xml(_XML)[2]
    assert retracted.is_retracted is True
    assert retracted.is_primary_article is False  # retracted ⇒ not counted as primary


def test_parse_bad_xml_is_empty():
    assert parse_pubmed_xml("<not valid") == []


def test_merge_dedupes_by_doi():
    oa = Work(id="oa", title="Same paper", doi="https://doi.org/10.1/x", cited_by_count=99)
    pm = Work(id="pm", title="Same paper", doi="10.1/x", cited_by_count=0)
    other = Work(id="o2", title="Different paper", doi="10.2/y")
    merged = _merge_works([oa], [pm, other])
    assert len(merged) == 2                        # pm deduped against oa by DOI
    assert merged[0].cited_by_count == 99          # OpenAlex copy kept (has citations)


def test_merge_dedupes_by_title_when_no_doi():
    a = Work(id="a", title="Cold plasma metrology of thin films")
    b = Work(id="b", title="Cold-plasma metrology of thin films!")  # punct/case differ
    assert len(_merge_works([a], [b])) == 1
    assert _work_key(a) == _work_key(b)
