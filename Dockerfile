FROM alpine:3.22

EXPOSE 3000

WORKDIR /usr/src/app

RUN apk add --no-cache python3 py3-pip python3-dev g++ make linux-headers uwsgi-python3 ffmpeg

COPY . .

RUN pip install --break-system-packages -r requirements.txt
RUN mkdir static
RUN mkdir data

CMD [ "uwsgi", "--socket", "0.0.0.0:3000", \
               "--uid", "uwsgi", \
               "--plugins", "python3", \
               "--protocol", "http", \
               "--wsgi", "app:app"]