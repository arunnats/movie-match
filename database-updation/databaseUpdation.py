import asyncio
import requests
import gzip
import pandas as pd
from io import BytesIO
from pymongo import MongoClient
import json
from aiohttp import ClientSession

async def fetch_tmdb_data(session, row, tmdb_api_key):
    movie_document = {
        "tconst": row['tconst'],
        "titleType": row['titleType'],
    }

    tmdb_id_url = f"https://api.themoviedb.org/3/find/{row['tconst']}?api_key={tmdb_api_key}&external_source=imdb_id"

    try:
        async with session.get(tmdb_id_url, timeout=15) as response:
            response.raise_for_status()
            tmdb_id_data = await response.json()
            tmdb_id_results = tmdb_id_data.get("movie_results", [])

            if tmdb_id_results:
                tmdb_id = tmdb_id_results[0].get("id", None)

                if tmdb_id:
                    keywords_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/keywords?api_key={tmdb_api_key}"
                    poster_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={tmdb_api_key}"

                    async with session.get(keywords_url) as keywords_response, session.get(poster_url) as poster_response:
                        keywords_data = await keywords_response.json()
                        poster_data = await poster_response.json()

                        if keywords_response.status == 200 and poster_response.status == 200:
                            movie_document.update({
                                "Title": row['originalTitle'],
                                "Keywords": [keyword['name'] for keyword in keywords_data.get("keywords", [])],
                                "PosterAlt": f"https://image.tmdb.org/t/p/w500{poster_data.get('poster_path', '')}" if poster_data.get('poster_path') else "",
                            })
                        else:
                            print(f"Failed to fetch Keywords or Poster data for {row['tconst']}.")
                else:
                    print(f"TMDB ID not found for {row['tconst']}.")
            
    except requests.exceptions.RequestException as e:
        print(f"Error fetching TMDB ID for {row['tconst']}: {e}")
    
    return movie_document

async def fetch_omdb_data(session, row, omdb_api_key):
    omdb_url = f"http://www.omdbapi.com/?i={row['tconst']}&apikey={omdb_api_key}"

    try:
        async with session.get(omdb_url) as omdb_response:
            omdb_response.raise_for_status()

            if omdb_response.status == 200:
                omdb_data = await omdb_response.json()
                return {
                    "Year": omdb_data.get("Year", ""),
                    "Rated": omdb_data.get("Rated", ""),
                    "Released": omdb_data.get("Released", ""),
                    "Runtime": omdb_data.get("Runtime", ""),
                    "Genre": omdb_data.get("Genre", ""),
                    "Director": omdb_data.get("Director", ""),
                    "Writer": omdb_data.get("Writer", ""),
                    "Actors": omdb_data.get("Actors", ""),
                    "Plot": omdb_data.get("Plot", ""),
                    "Language": omdb_data.get("Language", ""),
                    "Country": omdb_data.get("Country", ""),
                    "Poster": omdb_data.get("Poster", ""),
                    "RottenTomatoesRating": omdb_data["Ratings"][1]["Value"] if len(omdb_data.get("Ratings", [])) > 1 else "",
                    "IMDBRating": omdb_data.get("imdbRating", ""),
                }

            else:
                print(f"Failed to fetch OMDB data for {row['tconst']}.")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching OMDB data for {row['tconst']}: {e}")
    
    return None

async def main():
    print("Started Process")

    with open('dbConfig.json') as config_file:
        config = json.load(config_file)

    mongo_connection_string = config.get('mongo_connection_string', '')
    mongo_database_name = config.get('mongo_database_name', '')
    mongo_collection_name = config.get('mongo_collection_name', '')
    tmdb_api_key = config.get('tmdb_api_key', '')
    omdb_api_key = config.get('omdb_api_key', '')

    if not (mongo_connection_string and tmdb_api_key and omdb_api_key):
        print("MongoDB connection string or API keys not found in config.json.")
        exit()

    url = "https://datasets.imdbws.com/title.basics.tsv.gz"

    async with ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                with gzip.open(BytesIO(await response.read()), 'rt', encoding='utf-8') as file:
                    df = pd.read_csv(file, delimiter='\t')

                    client = MongoClient(mongo_connection_string)
                    db = client[mongo_database_name]
                    collection = db[mongo_collection_name]

                    last_inserted_document = collection.find_one(sort=[("tconst", -1)])

                    if last_inserted_document:
                        last_inserted_tconst = last_inserted_document["tconst"]
                    else:
                        last_inserted_tconst = None

                    if last_inserted_tconst:
                        movie_df = df[df['tconst'] > last_inserted_tconst].head(1000)
                    else:
                        movie_df = df[df['titleType'] == 'movie'].head(1000)

                    # Fetching TMDB data
                    tasks_tmdb = [fetch_tmdb_data(session, row, tmdb_api_key) for _, row in movie_df.iterrows()]
                    results_tmdb = await asyncio.gather(*tasks_tmdb)

                    # Fetching OMDB data
                    tasks_omdb = [fetch_omdb_data(session, row, omdb_api_key) for _, row in movie_df.iterrows()]
                    results_omdb = await asyncio.gather(*tasks_omdb)

                    # Merging TMDB and OMDB data and inserting into MongoDB
                    for result_tmdb, result_omdb in zip(results_tmdb, results_omdb):
                        if result_tmdb and result_omdb:
                            result_tmdb.update(result_omdb)
                            collection.insert_one(result_tmdb)
                            print(f"Document for {result_tmdb['tconst']} inserted into MongoDB successfully.")

                    client.close()

            else:
                print("Failed to download the file. Check the URL or try again later.")

if __name__ == "__main__":
    asyncio.run(main())
