FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /src
COPY . .
RUN mvn -B -Ddocker.skip=true -DskipTests -pl mall-admin,mall-portal -am package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /src/mall-admin/target/mall-admin-1.0-SNAPSHOT.jar /app/mall-admin.jar
COPY --from=build /src/mall-portal/target/mall-portal-1.0-SNAPSHOT.jar /app/mall-portal.jar
ENV SPRING_PROFILES_ACTIVE=local
ENV MALL_SERVICE=mall-admin
ENTRYPOINT ["sh", "-c", "exec java -jar /app/${MALL_SERVICE}.jar"]
