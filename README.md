# Car Bucket List

Rare cars worth tracking, and everything the internet says about each one, oldest to newest.

- `index.html`: the app (installable on phone and computer, opens offline).
- `data/cars.json`: the list. The app edits it through the GitHub API.
- `data/articles.json`: everything found so far. Only `research.py` writes it.
- `research.py`: searches Google News, Bing News and Bing web for each car's "search" phrases
  and keeps a result only if it contains a word from every "require" group (whole words,
  accents ignored).
- A specific car (one chassis of a model) is "part of" its model (`parent`) and named by its
  identifiers (`ids`: chassis numbers, nicknames, race numbers). It uses the model's searches and
  Must-mention lines unless it has its own, and an article must also mention one identifier.
  News searches that include the identifiers are trusted to have found one in the article text.
  Results are never deleted, and a link you hide with x never comes back.
- `.github/workflows/research.yml`: runs the research daily at about 6-7 AM New York time,
  after any change to the list, and when you press "Research now".

Run it on the Mac: `python3 research.py` then `python3 -m http.server` and open http://localhost:8000.
