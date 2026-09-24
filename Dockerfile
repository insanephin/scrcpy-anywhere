FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator

RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    openjdk-17-jdk \
    libgl1 \
    libpulse0 \
    libnss3 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxi6 \
    libxrandr2 \
    libxtst6 \
    socat \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=$JAVA_HOME/bin:$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator

RUN mkdir -p ${ANDROID_HOME}/cmdline-tools

RUN wget -q https://dl.google.com/android/repository/commandlinetools-linux-16111833_latest.zip \
    -O /tmp/cmdline-tools.zip \
    && unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_HOME}/cmdline-tools \
    && mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools \
       ${ANDROID_HOME}/cmdline-tools/latest \
    && rm /tmp/cmdline-tools.zip

RUN yes | sdkmanager --licenses

RUN sdkmanager \
    "platform-tools" \
    "emulator" \
    "platforms;android-36" \
    "system-images;android-36;google_apis_playstore;x86_64"

RUN echo "no" | avdmanager create avd \
    -n pixel \
    -k "system-images;android-36;google_apis_playstore;x86_64" \
    --force

RUN apt-get update && apt-get install -y python3-minimal \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5555

ENTRYPOINT ["/entrypoint.sh"]
