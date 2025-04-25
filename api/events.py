import pika
import json
import logging
from datetime import datetime
import os
import threading

logger = logging.getLogger(__name__)

class EventProducer:
    def __init__(self, rabbitmq_url):
        self.rabbitmq_url = rabbitmq_url
        self.connection = None
        self.channel = None
        self.exchanges = {}
        self.lock = threading.Lock()

    def connect(self):
        try:
            parameters = pika.URLParameters(self.rabbitmq_url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Объявляем обменники
            self.exchanges['user_events'] = self.channel.exchange_declare(
                exchange='user_events',
                exchange_type='topic',
                durable=True
            )
            self.exchanges['interaction_events'] = self.channel.exchange_declare(
                exchange='interaction_events',
                exchange_type='topic',
                durable=True
            )
            self.exchanges['matches'] = self.channel.exchange_declare(
                exchange='matches',
                exchange_type='fanout',
                durable=True
            )
            
            logger.info("Successfully connected to RabbitMQ")
        except Exception as e:
            logger.error(f"Error connecting to RabbitMQ: {str(e)}")
            raise

    def send_event(self, exchange_name, event_type, data):
        with self.lock:
            if not self.connection or not self.channel:
                self.connect()
            
            try:
                message = json.dumps({
                    'type': event_type,
                    'data': data,
                    'timestamp': datetime.utcnow().isoformat()
                })
                
                self.channel.basic_publish(
                    exchange=exchange_name,
                    routing_key=event_type,
                    body=message,
                    properties=pika.BasicProperties(
                        delivery_mode=2  # persistent
                    )
                )
                logger.info(f"Event {event_type} sent to {exchange_name}")
            except Exception as e:
                logger.error(f"Error sending event: {str(e)}")
                raise

    def close(self):
        if self.connection:
            self.connection.close()
            logger.info("RabbitMQ connection closed")

# Инициализация продюсера
rabbitmq_url = f"amqp://{os.getenv('RABBITMQ_USER', 'rabbit')}:{os.getenv('RABBITMQ_PASSWORD', 'rabbit')}@{os.getenv('RABBITMQ_HOST', 'rabbitmq')}:{os.getenv('RABBITMQ_PORT', '5672')}/"
event_producer = EventProducer(rabbitmq_url)

# Инициализация при старте Django
def init_rabbitmq():
    event_producer.connect()

# Закрытие при завершении Django
def close_rabbitmq():
    event_producer.close() 