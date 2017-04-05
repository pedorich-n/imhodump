#-*- coding: utf-8 -*-
import requests
import logging
import os
import shutil
import datetime
import argparse
import traceback

from lxml import etree
from json import dumps, loads
from math import ceil
from collections import OrderedDict
from urllib.parse import quote
from bs4 import BeautifulSoup


logging.basicConfig()
logger = logging.getLogger(os.path.basename(__file__))
logger.setLevel(logging.INFO)

VERSION = (0, 4, 0)


class ImhoDumper():

    SUBJECT_FILMS = 'films'
    SUBJECT_BOOKS = 'books'
    SUBJECT_GAMES = 'games'
    SUBJECT_SERIES = 'serials'

    TARGET_GOODREADS = 'Goodreads'
    TARGET_KINOPOISK = 'КиноПоиск'
    TARGETS = {
        TARGET_GOODREADS: 'https://www.goodreads.com/search?utf8=%E2%9C%93&q={term}&search_type=books',
        TARGET_KINOPOISK: 'http://www.kinopoisk.ru/index.php?first=no&what=&kp_query={term}',
    }

    SUBJECTS = {
        SUBJECT_FILMS: [TARGET_KINOPOISK],
        SUBJECT_BOOKS: [TARGET_GOODREADS],
        SUBJECT_GAMES: [],
        SUBJECT_SERIES: [TARGET_KINOPOISK]
    }

    URL_RATES_TPL = 'http://user.imhonet.ru/web.php?path=content/%(subject)s/rates/&user_domain=%(user_id)s&domain=user&page=%(page)s'
    START_FROM_RATING = 1

    def __init__(self, user_id, subject):
        self.user_id = user_id
        self.subject = subject
        self.output_filename = 'imho_rates_%s_%s.json' % (subject, user_id)

    def get_rates(self, json):
        items = json['user_rates']['content_rated']
        for item in items:
            heading = item['title']
            details_url = item['url']
            year = item['year']
            rating = item['rate']

            logger.info('Обрабатываем "%s" ...' % heading)

            req_details = requests.get(details_url)
            if req_details.status_code > 201:
                return

            soup = BeautifulSoup(req_details.text, "lxml")

            if self.subject == 'films':
                script = soup.find("script", {"id": "appState"}).string
                data = script.split("window.__app_state__ = ")[1][:-1]
                jsonData = loads(data)["data"]["content"]["content"]

                try:
                    title_orig = jsonData["title_original"]
                except (IndexError, AttributeError):
                    logger.debug('** Название на языке оригинала не заявлено')
                    title_orig = None

            elif self.subject == 'books':
                try:
                    title_orig = soup.find("div",{"class":"m-elementprimary-language"}).text
                except (IndexError, AttributeError):
                    logger.debug('** Название на языке оригинала не заявлено')
                    title_orig = None

                try:
                    author = soup.find("div", {"class":"m_row is-actors"}).find("a", {"class":"m_value"}).text
                except:
                    logger.info('** Автор не найден')
                    author = None



            logger.debug('Оригинальное название: %s' % title_orig)
            logger.debug('Год: %s' % year)

            if year is not None:
                heading = heading.replace('(%s)' % year, '').strip()

            item_data = {
                'title_ru': heading,
                'title_orig': title_orig,
                'rating': rating,
                'year': year,
                'details_url': details_url
            }

            if self.subject == 'films':
                item_data['country'] = ','.join(item['countries'])
            elif self.subject == 'books':
                item_data['author'] = author

            yield item_data

    def format_url(self, user_id, subject, page=1):
        return self.URL_RATES_TPL % {'user_id': self.user_id, 'subject': self.subject, 'page': page}

    def process_url(self, page_url, page, recursive=False):

        logger.info('Обрабатывается страница %s ...' % page_url)

        req = requests.get(page_url, headers={'Accept':'application/json'})
        if req.status_code > 201:
            return

        soup = BeautifulSoup(req.text, "lxml")

        script = soup.find("script", {"id": "appState"}).string
        data = script.split("window.__app_state__ = ")[1][:-1]
        jsonData = loads(data)

        try:
            json = jsonData["data"]["content"]
        except:
            return

        if len(json) == 0 or len(json['user_rates']['content_rated']) == 0:
            return

        next_page_url = self.format_url(self.user_id, self.subject, page + 1)

        logger.info('Следующая страница: %s' % next_page_url)

        yield from self.get_rates(json)

        if recursive and next_page_url is not None:
            yield from self.process_url(next_page_url, page + 1, recursive)

    def dump_to_file(self, filename, existing_items=None, start_from_rating=1):
        logger.info('Собираем оценки пользователя %s в файл %s' % (self.user_id, filename))

        with open(filename, 'w') as f:
            f.write('[')
            try:
                if existing_items:
                    f.write('%s,' % dumps(list(existing_items.values()), ensure_ascii=False, indent=4).strip('[]'))
                for item_data in self.process_url(self.format_url(self.user_id, self.subject), 1, True):
                    if not existing_items or item_data['details_url'] not in existing_items:
                        line = '%s,' % dumps(item_data, ensure_ascii=False, indent=4)
                        f.write(line)
                        f.flush()
            except BaseException as e:
                logger.info("Failed: %s" % e)
                logger.info(traceback.format_exc())
            finally:
                f.write('{}]')

    def load_from_file(self, filename):
        result = OrderedDict()
        if os.path.exists(filename):
            logger.info('Загружаем ранее собранные оценки пользователя %s из файла %s' % (self.user_id, filename))
            with open(filename, 'r') as f:
                text = f.read()
                f.close()
            try:
                data = loads(text, object_pairs_hook=OrderedDict)
            except:
                logger.info("Failed loading json")
                return None
            result = OrderedDict([(entry['details_url'], entry) for entry in data if entry])
        return result

    def make_html(self, filename):

        html_base = '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Оценки подраздела %(subject)s imhonet</title>
            <meta http-equiv="content-type" content="text/html; charset=utf-8" />
            <style>
                body {
                    color: #333;
                    font-family: Verdana, Arial, helvetica, sans-serif;
                }
                h1, h6 {
                    color: #999;
                }
                .rate_block {
                    border-bottom: 1px solid #eee;
                    padding: 0.4em;
                    padding-bottom: 1.2em;
                }
                .rating {
                    font-size: 1.5em;
                }
                .info, .description {
                    display: inline-block;
                    margin-left: 0.7em;
                    vertical-align: middle;
                }
                .rating .current {
                    color: #800;
                }
                .rating .total {
                    font-size: 0.7em;
                    color: #aaa;
                }
                .title_ru {
                    font-size: 1.7em;
                }
                .title_orig {
                    color: #aaa;
                }
                .links {
                    padding-top: 0.5em;
                    font-size: 0.8em;
                }
                .link {
                    display: inline-block;
                    margin-right: 0.5em;
                }
            </style>
        </head>
        <body>
            <h1>Оценки подраздела %(subject)s imhonet</h1>
            <h6>Всего оценок: %(rates_num)s</h6>
            %(rating_rows)s
        </body>
        </html>
        '''

        html_rating_row = '''
        <div class="rate_block">
            <div class="info">
                <div class="year">%(year)s</div>
                <div class="rating">
                    <span class="current">%(rating)s</span><span class="total">/10</span>
                    <span class="current">%(rating_five)s</span><span class="total">/5</span>
                </div>
            </div>
            <div class="description">
                <div class="titles">
                    <div class="title_ru">
                        <label>%(title_ru)s</label>
                    </div>
                    <div class="title_orig">%(title_orig)s</div>
                </div>
                <div class="links">
                    Поиск:
                    %(links)s
                </div>
            </div>
        </div>
        '''

        html_link_row = '''
        <div class="link"><a href="%(link)s" target="_blank">%(title)s</a></div>
        '''

        records = self.load_from_file(filename)

        rating_rows = []
        for record in records.values():
            links = []
            for link_type in self.SUBJECTS[self.subject]:
                for title_type in ('title_orig', 'title_ru'):
                    if record[title_type]:
                        links.append(html_link_row % {
                            'link': self.TARGETS[link_type].replace('{term}', quote(record[title_type])),
                            'title': '%s (%s)' % (link_type, title_type)
                        })

            record['links'] = '\n'.join(links)
            record['rating_five'] = ceil(record['rating'] / 2)
            del record['details_url']
            rating_rows.append(html_rating_row % record)

        target_file = '%s.html' % os.path.splitext(filename)[0]
        logger.info('Создаём html файл с оценками: %s' % target_file)
        with open(target_file, 'w') as f:
            f.write(html_base % {'subject': self.subject, 'rates_num': len(records), 'rating_rows': '\n'.join(rating_rows)})

    def backup_json(self, filename):
        target_filename = '%s.bak%s' % (filename, datetime.datetime.isoformat(datetime.datetime.now()))
        logger.info('Делаем резервную копию файла с оценками: %s' % target_filename)
        shutil.copy(filename, target_filename)

    def dump(self):
        existing_items = self.load_from_file(self.output_filename)
        if existing_items:
            self.backup_json(self.output_filename)
        self.dump_to_file(self.output_filename, existing_items=existing_items, start_from_rating=self.START_FROM_RATING)
        self.make_html(self.output_filename)


if __name__ == '__main__':

    args_parser = argparse.ArgumentParser()
    args_parser.add_argument('user_id', help='ID пользователя imhonet')
    args_parser.add_argument('subject', help='Категория: %s' % ', '.join([s for s in ImhoDumper.SUBJECTS.keys()]))
    args_parser.add_argument('--html_only', help='Указывает, что требуется только экспорт уже имеющегося файла с оценками в html', action='store_true')

    parsed = args_parser.parse_args()

    dumper = ImhoDumper(parsed.user_id, parsed.subject)
    if parsed.html_only:
        dumper.make_html(dumper.output_filename)
    else:
        dumper.dump()
