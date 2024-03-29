import os
import configparser
import random

import telegram
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

import newsbot.config as config
import newsbot.database as database
import newsbot.network as network


class Bot:
    """Класс, представляющий собой телеграмм бота
    """

    def __init__(self, a_token=None, a_use_context=None):
        self.db = database.DatabaseController()

        # Настраиваем конфигурацию бота, считывая конфигурационный файл
        self._config = configparser.ConfigParser()
        if not os.path.exists(config.BOT_SETTINGS_PATH):
            with open(config.BOT_SETTINGS_PATH, 'w') as configfile:
                config.DEFAULT_SETTINGS.write(configfile)
            self._config = config.DEFAULT_SETTINGS
        self._config.read(config.BOT_SETTINGS_PATH)

        self._token = a_token if a_token else self._config['DEFAULT']['token']
        self._use_context = a_use_context if a_use_context else self._config['DEFAULT']['use_context']

        self._parser = network.NewsSiteParser(self.config['news_url'])

        self._topics = self._parser.get_news_topics(self.config['news_topic_class'])
        self._sync_topics(self._topics)

        self._updater = Updater(token=self._token, use_context=self._use_context)
        self._dispatcher = self._updater.dispatcher

        handler = MessageHandler(Filters.text | Filters.command, self.__handle_message)
        self._dispatcher.add_handler(handler)

        self._main_buttons = [
            [
                'Изменить интервал ⏱',
                'Выбрать топики 📖',
            ],
            [
                'Хочу новости вне очереди! 🐷'
            ]
        ]

        # Состояния бота
        self._states = [
            self.__s_start,
            self.__s_main,
            self.__s_typing_interval,
            self.__s_choosing_topics,
        ]

    @property
    def config(self):
        return dict(self._config['DEFAULT'])

    def start(self):
        """Функция запуска работы бота
        """

        self._updater.start_polling()
        self._updater.idle()

    def stop(self):
        """Функция остановки работы бота
        """

        self._updater.stop()

    def _sync_topics(self, a_topics):
        db_topics = self.db.get_topics()
        for topic in a_topics:
            if topic not in db_topics:
                self.db.add_topic(topic)

    def __handle_message(self, a_update: Update, a_context: CallbackContext):
        """Функция обработки всех поступающих сообщений боту
        """

        user_id = a_update.effective_user.id
        self.db.add_user_if_not_exists(user_id)

        user_state = self.db.get_user(user_id).state
        self._states[user_state](a_update, a_context)

    def __s_start(self, a_update: Update, a_context: CallbackContext):
        """Первое состояние бота, состояние знакомства
        """

        user_id = a_update.effective_user.id
        user_name = a_update.effective_user.name

        self.db.update_user_state(a_user_id=user_id, a_state=1)

        text = f'Привет, {user_name}!\nЭтот бот предназначен для получения новостей с habr.com по вашим ' \
               f'предпочтениям.\nВы также можете задать с помощью кнопок интервал отправки новостей и интересующих ' \
               f'для вас топиков новостей.'
        a_context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=self.__get_main_keyboard(a_update, a_context)
        )

    def __s_main(self, a_update: Update, a_context: CallbackContext):
        """Второе состояние бота, обработка поступающих сообщений (команд)
        """

        user_id = a_update.effective_user.id
        user_text = a_update.message.text

        command_found = False
        for btns_list in self.__format_main_keyboard(a_update, a_context):
            if user_text in btns_list:
                command_found = True
                break

        if not command_found:
            text = 'Неизвестная команда 🤥. Воспользуйся кнопками ниже!'
            a_context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=self.__get_main_keyboard(a_update, a_context)
            )
        else:
            if user_text == 'Изменить интервал ⏱':
                self.db.update_user_state(a_user_id=user_id, a_state=2)

                text = 'Введите новое значение интервала отправки новостей (в минутах).'
                a_context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=ReplyKeyboardMarkup([['Отмена']])
                )
            elif user_text == 'Выбрать топики 📖':
                self.db.update_user_state(a_user_id=user_id, a_state=3)

                text = 'Выберите интересующие вас топики новостей.\n✅ – означает, что топик выбран, ❌ – обратное. Для ' \
                       'того, чтобы выйти, выберите кнопку "Закончить выбор".'
                a_context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=self.__get_keyboard_tor_edit_topics(user_id)
                )
            elif user_text == 'Хочу новости вне очереди! 🐷':
                self.__get_news(a_context, a_update)
            elif user_text in ['Выключить отправку новостей 🔕', 'Включить отправку новостей 🔔']:
                if self.__has_user_job_to_send_news(a_update, a_context):
                    self.__stop_job_to_send_news(a_update, a_context)
                else:
                    self.__start_job_to_send_news(a_update, a_context)

    def __s_typing_interval(self, a_update: Update, a_context: CallbackContext):
        """Третье состояние бота, изменение интервала отправки новостей
        """

        user_id = a_update.effective_user.id
        user_text = a_update.message.text

        if user_text.isdigit() and int(user_text) > 0:
            self.db.update_user_interval(a_user_id=user_id, a_interval=user_text)
            self.db.update_user_state(a_user_id=user_id, a_state=1)
            self.__start_job_to_send_news(a_update, a_context)

            text = f'Интервал отправки новостей успешно изменен. Текущее значение интервала: {user_text} мин.'
            a_context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=self.__get_main_keyboard(a_update, a_context)
            )
        elif user_text == 'Отмена':
            self.db.update_user_state(a_user_id=user_id, a_state=1)
            interval = self.db.get_user(user_id).interval

            text = f'Операция по изменению интервала отменена. Текущее значение интервала: {interval} мин.'
            a_context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=self.__get_main_keyboard(a_update, a_context)
            )
        else:
            text = 'Некорректное значение для интервала! Попробуй еще раз.'
            a_context.bot.send_message(
                chat_id=user_id,
                text=text,
            )

    def __s_choosing_topics(self, a_update: Update, a_context: CallbackContext):
        """Четвертое состояние бота, выбор топиков новостей
        """

        user_id = a_update.effective_user.id
        user_topic = a_update.message.text

        if user_topic[:-1] in self._topics:
            user_topic = user_topic[:-1]
            if self.db.has_user_topic(user_id, user_topic):
                self.db.delete_topic_of_user(a_user_id=user_id, a_topic_name=user_topic)
                text = f'Топик "{user_topic}" успешно удален!'
            else:
                self.db.add_topic_to_user(a_user_id=user_id, a_topic_name=user_topic)
                text = f'Топик "{user_topic}" успешно выбран!'
            keyboard = self.__get_keyboard_tor_edit_topics(user_id)
        elif user_topic == 'Закончить выбор':
            self.db.update_user_state(a_user_id=user_id, a_state=1)
            text = 'Операция по выбору топиков завершена! Изменения зафиксированы.'
            keyboard = self.__get_main_keyboard(a_update, a_context)
        else:
            text = 'Неизвестный топик 🤥. Для выбора топика воспользуйтесь кнопками!'
            keyboard = self.__get_keyboard_tor_edit_topics(user_id)

        a_context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=keyboard
        )

    def __get_keyboard_tor_edit_topics(self, a_user_id):
        """Функция, возвращающая набор кнопок для выбора топиков новостей
        """

        user_chosen_topics = self.db.get_users_topics(a_user_id)

        btns_text = []
        for topic in self._topics.keys():
            if topic in user_chosen_topics:
                btns_text.append(f'{topic}✅')
            else:
                btns_text.append(f'{topic}❌')

        keyboard = []
        tmp = []

        for idx, text in enumerate(btns_text):
            if (idx + 1) % 3 != 0:
                tmp.append(KeyboardButton(text))
            else:
                keyboard.append(tmp.copy())
                tmp.clear()

        if len(tmp) + 1 % 3 == 0:
            keyboard.append(tmp.copy())
            tmp.clear()
        tmp.append(KeyboardButton('Закончить выбор'))
        keyboard.append(tmp)

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def __has_user_job_to_send_news(a_update: Update, a_context: CallbackContext):
        """Функция, возвращающая, установлена ли функция отправки новостей для пользователя
        """

        user_id = a_update.effective_user.id
        jobs = a_context.job_queue.get_jobs_by_name(str(user_id))
        return len(jobs) > 0

    def __format_main_keyboard(self, a_update: Update, a_context: CallbackContext):
        """Функция, возвращающая набор имен для кнопок для основных кнопок
        """

        keyboard = self._main_buttons.copy()

        if self.__has_user_job_to_send_news(a_update, a_context):
            keyboard.append(['Выключить отправку новостей 🔕'])
        else:
            keyboard.append(['Включить отправку новостей 🔔'])

        return keyboard

    def __get_main_keyboard(self, a_update: Update, a_context: CallbackContext):
        """Функция, возвращающая готовый набор кнопок в обертку ReplyKeyboardMarkup
        """

        return ReplyKeyboardMarkup(self.__format_main_keyboard(a_update, a_context))

    def __get_news(self, a_context: CallbackContext, a_update: Update = None):
        """Функция, возвращающая новость пользователю
        """

        if a_update:
            user_id = a_update.effective_user.id
        else:
            user_id = a_context.job.context
        user_topics = self.db.get_users_topics(user_id)

        page = None
        if len(user_topics) == 0:
            posts_topic = list(self._topics.keys())[0]
        else:
            posts_topic = random.choice(user_topics)

        user_shown_list = self.db.get_user_shown_posts(a_user_id=user_id, a_topic_name=posts_topic)
        news = self._parser.get_news(self._topics[posts_topic], a_shown_list=user_shown_list, a_page=page,
                                     a_limit_preview_text=int(self.config['news_limit_text']),
                                     a_additional_content_id=self.config['news_additional_content_id'])
        page = 0
        while len(news) == 0:
            page += 1
            news = self._parser.get_news(self._topics[posts_topic], a_shown_list=user_shown_list, a_page=page,
                                         a_limit_preview_text=int(self.config['news_limit_text']))

        self.db.add_shown_post(a_user_id=user_id, a_topic_name=posts_topic, a_post_id=news['id'])

        text = f'*{news["title"]}*\n\n{news["text"]}\n\nЗаинтересовало? Читай дальше по ссылке: {news["url"]}'
        a_context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=self.__get_main_keyboard(a_update, a_context),
            parse_mode=telegram.ParseMode.MARKDOWN,
        )

    def __start_job_to_send_news(self, a_update: Update, a_context: CallbackContext):
        """Функция запуска автоматической отправки новостей пользователю через опредленный интервал
        """

        user_id = a_update.effective_user.id
        interval = self.db.get_user(user_id).interval

        a_context.job_queue.run_repeating(self.__get_news, interval * 60, context=str(user_id), name=str(user_id))

        text = f'Автоматическая отправка новостей включена! Новости будут отправляться каждые {interval} мин.'
        a_context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=self.__get_main_keyboard(a_update, a_context)
        )

    def __stop_job_to_send_news(self, a_update: Update, a_context: CallbackContext):
        """Функция деактивации автоматической отправки новостей пользователю
        """

        user_id = a_update.effective_user.id
        jobs = a_context.job_queue.get_jobs_by_name(user_id)
        if jobs:
            for job in jobs:
                job.schedule_removal()

        text = f'Автоматическая отправка новостей выключена!'
        a_context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=self.__get_main_keyboard(a_update, a_context)
        )
