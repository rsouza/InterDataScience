# Databricks notebook source
# MAGIC %md
# MAGIC # Random Forests and Hyperparameter Tuning
# MAGIC 
# MAGIC Now let's take a look at how to tune random forests using grid search and cross validation in order to find the optimal hyperparameters.  Using the Databricks Runtime for ML, MLflow automatically logs your experiments with the SparkML cross-validator!
# MAGIC 
# MAGIC ## ![Spark Logo Tiny](https://files.training.databricks.com/images/105/logo_spark_tiny.png) In this lesson you:<br>
# MAGIC  - Tune hyperparameters using Grid Search
# MAGIC  - Optimize a SparkML pipeline

# COMMAND ----------

# MAGIC %md
# MAGIC #### Importing modules and disabling MLflow  

# COMMAND ----------

import os
import mlflow
mlflow.autolog(disable=True)

# COMMAND ----------

from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.ml.regression import RandomForestRegressor
from pyspark.ml import Pipeline

# COMMAND ----------

# MAGIC %md
# MAGIC ### Setting the default database and user name  
# MAGIC ##### Substitute "renato" by your name in the `username` variable.

# COMMAND ----------

## Put your name here
username = "renato"

dbutils.widgets.text("username", username)
spark.sql(f"CREATE DATABASE IF NOT EXISTS dsacademy_embedded_wave3_{username}")
spark.sql(f"USE dsacademy_embedded_wave3_{username}")
spark.conf.set("spark.sql.shuffle.partitions", 40)

spark.sql("SET spark.databricks.delta.formatCheck.enabled = false")
spark.sql("SET spark.databricks.delta.properties.defaults.autoOptimize.optimizeWrite = true")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Reading Dataset

# COMMAND ----------

deltaPath = os.path.join("/", "tmp", username)    #If we were writing to the root folder and not to the DBFS
if not os.path.exists(deltaPath):
    os.mkdir(deltaPath)
    
print(deltaPath)

airbnbDF = spark.read.format("delta").load(deltaPath)

# COMMAND ----------

# MAGIC %md
# MAGIC #### Imputing Null Values  
# MAGIC [Python](https://spark.apache.org/docs/3.1.1/api/python/reference/api/pyspark.sql.DataFrame.fillna.html)  

# COMMAND ----------

airbnbDF = airbnbDF.fillna(0)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Repeating the steps from previous Notebook

# COMMAND ----------

(trainDF, testDF) = airbnbDF.randomSplit([.8, .2], seed=42)

# COMMAND ----------

categoricalCols = [field for (field, dataType) in trainDF.dtypes if dataType == "string"]
indexOutputCols = [x + "Index" for x in categoricalCols]

stringIndexer = StringIndexer(inputCols=categoricalCols, outputCols=indexOutputCols, handleInvalid="skip")

numericCols = [field for (field, dataType) in trainDF.dtypes if ((dataType == "double") & (field != "price"))]
assemblerInputs = indexOutputCols + numericCols
vecAssembler = VectorAssembler(inputCols=assemblerInputs, outputCol="features")

# COMMAND ----------

rf = RandomForestRegressor(labelCol="price", maxBins=60)
stages = [stringIndexer, vecAssembler, rf]
pipeline = Pipeline(stages=stages)

# COMMAND ----------

# MAGIC %md
# MAGIC ## ParamGrid
# MAGIC 
# MAGIC First let's take a look at the various hyperparameters we could tune for random forest.
# MAGIC 
# MAGIC **Pop quiz:** what's the difference between a parameter and a hyperparameter?

# COMMAND ----------

print(rf.explainParams())

# COMMAND ----------

# MAGIC %md
# MAGIC There are a lot of hyperparameters we could tune, and it would take a long time to manually configure.
# MAGIC 
# MAGIC Instead of a manual (ad-hoc) approach, let's use Spark's `ParamGridBuilder` to find the optimal hyperparameters in a more systematic approach [Python](https://spark.apache.org/docs/latest/api/python/pyspark.ml.html#pyspark.ml.tuning.ParamGridBuilder)/[Scala](https://spark.apache.org/docs/latest/api/scala/#org.apache.spark.ml.tuning.ParamGridBuilder).
# MAGIC 
# MAGIC Let's define a grid of hyperparameters to test:
# MAGIC   - `maxDepth`: max depth of each decision tree (Use the values `2, 5`)
# MAGIC   - `numTrees`: number of decision trees to train (Use the values `5, 10`)
# MAGIC 
# MAGIC `addGrid()` accepts the name of the parameter (e.g. `rf.maxDepth`), and a list of the possible values (e.g. `[2, 5]`).

# COMMAND ----------

from pyspark.ml.tuning import ParamGridBuilder

paramGrid = (ParamGridBuilder()
            .addGrid(rf.maxDepth, [2, 5])
            .addGrid(rf.numTrees, [5, 10])
            .build())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cross Validation
# MAGIC 
# MAGIC We are also going to use 3-fold cross validation to identify the optimal hyperparameters.
# MAGIC 
# MAGIC ![crossValidation](https://files.training.databricks.com/images/301/CrossValidation.png)
# MAGIC 
# MAGIC With 3-fold cross-validation, we train on 2/3 of the data, and evaluate with the remaining (held-out) 1/3. We repeat this process 3 times, so each fold gets the chance to act as the validation set. We then average the results of the three rounds.

# COMMAND ----------

# MAGIC %md
# MAGIC We pass in the `estimator` (pipeline), `evaluator`, and `estimatorParamMaps` to `CrossValidator` so that it knows:
# MAGIC - Which model to use
# MAGIC - How to evaluate the model
# MAGIC - What hyperparameters to set for the model
# MAGIC 
# MAGIC We can also set the number of folds we want to split our data into (3), as well as setting a seed so we all have the same split in the data [Python](https://spark.apache.org/docs/latest/api/python/pyspark.ml.html#pyspark.ml.tuning.CrossValidator)/[Scala](https://spark.apache.org/docs/latest/api/scala/#org.apache.spark.ml.tuning.CrossValidator).

# COMMAND ----------

from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.ml.tuning import CrossValidator

evaluator = RegressionEvaluator(labelCol="price", predictionCol="prediction")

cv = CrossValidator(estimator=pipeline, 
                    evaluator=evaluator, 
                    estimatorParamMaps=paramGrid, 
                    numFolds=3, seed=42)

# COMMAND ----------

# MAGIC %md
# MAGIC **Question**: How many models are we training right now?

# COMMAND ----------

cvModel = cv.fit(trainDF)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parallelism Parameter
# MAGIC 
# MAGIC Hmmm... that took a long time to run. That's because the models were being trained sequentially rather than in parallel!
# MAGIC 
# MAGIC In Spark 2.3, a [parallelism](https://spark.apache.org/docs/latest/api/python/pyspark.ml.html#pyspark.ml.tuning.CrossValidator.parallelism) parameter was introduced. From the docs: `the number of threads to use when running parallel algorithms (>= 1)`.
# MAGIC 
# MAGIC Let's set this value to 4 and see if we can train any faster. The Spark [docs](https://spark.apache.org/docs/latest/ml-tuning.html) recommend a value between 2-10.

# COMMAND ----------

cvModel = cv.setParallelism(2).fit(trainDF)

# COMMAND ----------

# MAGIC %md
# MAGIC **Question**: Hmmm... that still took a long time to run. Should we put the pipeline in the cross validator, or the cross validator in the pipeline?
# MAGIC 
# MAGIC It depends if there are estimators or transformers in the pipeline. If you have things like StringIndexer (an estimator) in the pipeline, then you have to refit it every time if you put the entire pipeline in the cross validator.
# MAGIC 
# MAGIC However, if there is any concern about data leakage from the earlier steps, the safest thing is to put the pipeline inside the CV, not the other way. CV first splits the data and then .fit() the pipeline. If it is placed at the end of the pipeline, we potentially can leak the info from hold-out set to train set.

# COMMAND ----------

cv = CrossValidator(estimator=rf, 
                    evaluator=evaluator, 
                    estimatorParamMaps=paramGrid, 
                    numFolds=3, 
                    parallelism=2, 
                    seed=42)

stagesWithCV = [stringIndexer, vecAssembler, cv]
pipeline = Pipeline(stages=stagesWithCV)

pipelineModel = pipeline.fit(trainDF)

# COMMAND ----------

# MAGIC %md
# MAGIC Let's take a look at the model with the best hyperparameter configuration

# COMMAND ----------

list(zip(cvModel.getEstimatorParamMaps(), cvModel.avgMetrics))

# COMMAND ----------

predDF = pipelineModel.transform(testDF)

rmse = evaluator.evaluate(predDF)
r2 = evaluator.setMetricName("r2").evaluate(predDF)
print(f"RMSE is {rmse}")
print(f"R2 is {r2}")

# COMMAND ----------

# MAGIC %md
# MAGIC Progress!  Looks like we're out-performing decision trees.

# COMMAND ----------

# MAGIC %md
# MAGIC Code modified and enhanced from 2020 Databricks, Inc. All rights reserved.<br/>
# MAGIC Apache, Apache Spark, Spark and the Spark logo are trademarks of the <a href="http://www.apache.org/">Apache Software Foundation</a>.<br/>
# MAGIC <br/>
# MAGIC <a href="https://databricks.com/privacy-policy">Privacy Policy</a> | <a href="https://databricks.com/terms-of-use">Terms of Use</a> | <a href="http://help.databricks.com/">Support</a>
